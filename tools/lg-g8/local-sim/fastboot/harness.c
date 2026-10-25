#include "host_shim.h"
#include <signal.h>

static const char *scenario;
static unsigned serial_calls, event_calls, transfer_calls;
static BOOLEAN stopped;
static EFI_EVENT_NOTIFY fatal_callback;
static USB_DEVICE_DESCRIPTOR_SET *retained;
static EFI_BLOCK_IO_PROTOCOL disk;
static EFI_BLOCK_IO_MEDIA media;
static DeviceInfo devinfo = {"host-only-bootloader", "host-only-radio"};
static struct { VOID *ptr; UINTN size; BOOLEAN transfer; } allocations[256];
static UINTN allocation_count;

extern VOID HostSetDownloadState(BOOLEAN IsData);
extern VOID HostSetFinished(VOID);

static BOOLEAN is(const char *name) { return strcmp(scenario, name) == 0; }

static VOID trace(const char *format, ...) {
  va_list args;
  va_start(args, format);
  vprintf(format, args);
  va_end(args);
  putchar('\n');
  fflush(stdout);
}

static VOID hang(const char *name) {
  trace("HANG %s", name);
  for (;;) pause();
}

static VOID *track(UINTN size, BOOLEAN transfer) {
  VOID *pointer = malloc(size);
  assert(pointer && allocation_count < 256);
  allocations[allocation_count].ptr = pointer;
  allocations[allocation_count].size = size;
  allocations[allocation_count++].transfer = transfer;
  return pointer;
}

static VOID release(VOID *pointer) {
  for (UINTN i = 0; i < allocation_count; i++) {
    if (allocations[i].ptr == pointer) {
      free(pointer);
      allocations[i].ptr = NULL;
      return;
    }
  }
  assert(!"Unknown or duplicate allocation passed to release");
}

VOID *AllocateZeroPool(UINTN Size) {
  if (serial_calls >= 2) {
    if (is("descriptor-pool-error")) {
      trace("AllocateZeroPool descriptor injected-error");
      return NULL;
    }
  }
  VOID *p = track(Size, FALSE);
  memset(p, 0, Size);
  return p;
}

VOID FreePool(VOID *Buffer) { release(Buffer); }

static EFI_STATUS EFIAPI create_event(UINT32 Type, EFI_TPL Tpl, EFI_EVENT_NOTIFY Notify, VOID *Context, EFI_EVENT *Event) {
  trace("BS.CreateEvent fatal-send");
  if (is("commands-event-error")) return EFI_DEVICE_ERROR;
  fatal_callback = Notify;
  *Event = track(8, FALSE);
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI create_event_ex(UINT32 Type, EFI_TPL Tpl, EFI_EVENT_NOTIFY Notify, CONST VOID *Context, CONST EFI_GUID *Group, EFI_EVENT *Event) {
  trace("BS.CreateEventEx init-usb-controller guid=%08x", Group->Data1);
  if (is("create-controller-error")) return EFI_DEVICE_ERROR;
  *Event = track(8, FALSE);
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI signal_event(EFI_EVENT Event) {
  trace("BS.SignalEvent%s", is("signal-error-ignored") ? " injected-error" : "");
  return is("signal-error-ignored") ? EFI_DEVICE_ERROR : EFI_SUCCESS;
}

static EFI_STATUS EFIAPI close_event(EFI_EVENT Event) {
  trace("BS.CloseEvent");
  release(Event);
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI watchdog(UINTN Timeout, UINT64 Code, UINTN Size, CHAR16 *Data) {
  trace("BS.SetWatchdogTimer timeout=%lu", Timeout);
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI memory_map(UINTN *Size, EFI_MEMORY_DESCRIPTOR *Map, UINTN *Key, UINTN *DescriptorSize, UINT32 *Version) {
  *DescriptorSize = sizeof(*Map);
  *Version = 1;
  *Key = 1;
  if (Map == NULL) {
    trace("BS.GetMemoryMap query");
    *Size = 3 * sizeof(*Map);
    return is("memory-map-query-error") ? EFI_DEVICE_ERROR : EFI_BUFFER_TOO_SMALL;
  }
  trace("BS.GetMemoryMap fill largest=%s", is("memory-fragmented") ? "64MiB" : "128MiB");
  assert(*Size >= 3 * sizeof(*Map));
  memset(Map, 0, 3 * sizeof(*Map));
  *Size = 3 * sizeof(*Map);
  for (unsigned i = 0; i < 3; i++) {
    Map[i].Type = EfiConventionalMemory;
    Map[i].NumberOfPages = (is("memory-fragmented") ? 64 : (i == 1 ? 128 : 32)) * 1024 * 1024 / EFI_PAGE_SIZE;
  }
  return EFI_SUCCESS;
}

static VOID EFIAPI set_mem(VOID *Buffer, UINTN Size, UINT8 Value) {
  trace("BS.SetMem size=%lu", Size);
  memset(Buffer, Value, Size);
}

static EFI_STATUS EFIAPI get_variable(CHAR16 *Name, EFI_GUID *Guid, UINT32 *Attrs, UINTN *Size, VOID *Data) {
  trace("RT.GetVariable UsbfnMaxSpeed");
  assert(*Size == sizeof(EFI_USB_BUS_SPEED));
  *(EFI_USB_BUS_SPEED *)Data = (is("usb-superplus") || is("descriptor-serial-error-superplus")) ? UsbBusSpeedSuperPlus : UsbBusSpeedHigh;
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI transfer_allocate(UINTN Size, VOID **Buffer) {
  transfer_calls++;
  const char *kind = transfer_calls == 1 ? "download" : transfer_calls == 2 ? "rx" : "tx";
  /* Retry failures consume attempts before the fixed RX/TX allocations. */
  if (is("download-retry")) kind = transfer_calls <= 2 ? "download" : transfer_calls == 3 ? "rx" : "tx";
  if (is("download-exhausted")) kind = "download";
  if (is("hang-download") && transfer_calls == 1) hang("Usb.AllocateTransferBuffer download");
  if (is("download-exhausted") || (is("download-retry") && transfer_calls == 1)) {
    trace("Usb.AllocateTransferBuffer %s size=%lu injected-error", kind, Size);
    return EFI_OUT_OF_RESOURCES;
  }
  if ((is("rx-error") && transfer_calls == 2) || (is("tx-error") && transfer_calls == 3)) {
    trace("Usb.AllocateTransferBuffer %s injected-error", kind);
    return EFI_OUT_OF_RESOURCES;
  }
  trace("Usb.AllocateTransferBuffer %s size=%lu", kind, Size);
  *Buffer = track(Size, TRUE);
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI transfer_free(VOID *Buffer) {
  if (stopped && is("strict-stop-lifetime")) {
    trace("Usb.FreeTransferBuffer after-stop rejected");
    return EFI_INVALID_PARAMETER;
  }
  trace("Usb.FreeTransferBuffer%s", stopped ? " after-stop" : "");
  release(Buffer);
  return EFI_SUCCESS;
}

static EFI_STATUS usb_start_ex(USB_DEVICE_DESCRIPTOR_SET *Set) {
  if (is("hang-startex")) hang("Usb.StartEx");
  if (is("start-error")) {
    trace("Usb.StartEx injected-error");
    return EFI_DEVICE_ERROR;
  }
  assert(Set->StringDescriptorCount == 5);
  /* The real driver must consume these pointers. Sanitizers inspect the same
   * real source outputs here rather than a replacement descriptor builder. */
  trace("Usb.StartEx bcdUSB=%04x VID=%04x PID=%04x", Set->SSDeviceDescriptor->BcdUSB,
        Set->DeviceDescriptor->IdVendor, Set->DeviceDescriptor->IdProduct);
  assert(Set->DeviceDescriptor->NumConfigurations == 1);
  assert(((EFI_USB_CONFIG_DESCRIPTOR *)Set->Descriptors[0])->NumInterfaces == 1);
  assert(((EFI_USB_CONFIG_DESCRIPTOR *)Set->SSDescriptors[0])->NumInterfaces == 1);
  retained = Set;
  return EFI_SUCCESS;
}

static EFI_STATUS usb_send(UINT8 Endpoint, UINTN Size, VOID *Buffer) {
  trace("Usb.Send endpoint=%u size=%lu", Endpoint, Size);
  assert(Buffer != NULL);
  return EFI_SUCCESS;
}

static EFI_STATUS usb_stop(VOID) {
  trace("Usb.Stop");
  stopped = TRUE;
  return EFI_SUCCESS;
}

static EFI_STATUS EFIAPI usb_event(USB_DEVICE_EVENT *Event, UINTN *Size, USB_DEVICE_EVENT_DATA *Payload) {
  event_calls++;
  if (is("hang-event")) hang("Usb.HandleEvent");
  trace("Usb.HandleEvent count=%u", event_calls);
  if (is("descriptor-retained-by-firmware")) {
    trace("Usb.HandleEvent read-retained-descriptor-pointers");
    volatile UINT8 value = ((EFI_USB_CONFIG_DESCRIPTOR *)retained->Descriptors[0])->Length;
    (void)value;
  }
  *Event = UsbDeviceEventNoEvent;
  *Size = sizeof(*Payload);
  memset(Payload, 0, sizeof(*Payload));
  if (event_calls >= 2) {
    trace("FastbootFatal bounded-exit via registered fatal callback");
    assert(fatal_callback);
    fatal_callback(NULL, NULL);
    return EFI_SUCCESS;
  }
  if (is("event-error-ignored")) {
    trace("Usb.HandleEvent injected-error");
    return EFI_DEVICE_ERROR;
  }
  if (is("rx-command") || is("rx-data") || is("tx-complete") || is("rx-cancel") || is("rx-transfer-error")) {
    *Event = UsbDeviceEventTransferNotification;
    Payload->TransferOutcome.EndpointIndex = is("tx-complete") ? 0x81 : 1;
    Payload->TransferOutcome.Status = is("rx-cancel") ? UsbDeviceTransferStatusCancelled : is("rx-transfer-error") ? UsbDeviceTransferStatusCompleteError : UsbDeviceTransferStatusCompleteOK;
    Payload->TransferOutcome.BytesCompleted = 8;
    Payload->TransferOutcome.DataBuffer = GetFastbootDeviceData()->gRxBuffer;
    HostSetDownloadState(is("rx-data"));
    if (is("rx-cancel")) trace("Usb.HandleEvent transfer-cancel");
    if (is("rx-transfer-error")) trace("Usb.HandleEvent transfer-error");
  } else {
    *Event = UsbDeviceEventDeviceStateChange;
    Payload->DeviceState = UsbDeviceStateConnected;
  }
  return EFI_SUCCESS;
}

static EFI_USB_DEVICE_PROTOCOL usb = {
  .Revision = EFI_USB_DEVICE_PROTOCOL_REVISION,
  .Send = usb_send,
  .HandleEvent = usb_event,
  .AllocateTransferBuffer = transfer_allocate,
  .FreeTransferBuffer = transfer_free,
  .Stop = usb_stop,
  .StartEx = usb_start_ex,
};

static EFI_STATUS EFIAPI locate(EFI_GUID *Guid, VOID *Registration, VOID **Protocol) {
  trace("BS.LocateProtocol guid=%08x", Guid->Data1);
  if (is("locate-error")) return EFI_NOT_FOUND;
  assert(Guid->Data1 == 0xd9d9ce48);
  if (is("protocol-legacy-no-startex")) {
    trace("BS.LocateProtocol legacy-with-null-StartEx");
    usb.Revision = 0;
    usb.StartEx = NULL;
  }
  *Protocol = &usb;
  return EFI_SUCCESS;
}

static EFI_BOOT_SERVICES services = {
  .CreateEventEx = create_event_ex, .CreateEvent = create_event,
  .SignalEvent = signal_event, .CloseEvent = close_event, .LocateProtocol = locate,
  .SetWatchdogTimer = watchdog, .GetMemoryMap = memory_map, .SetMem = set_mem,
};
static EFI_RUNTIME_SERVICES runtime = {.GetVariable = get_variable};
EFI_BOOT_SERVICES *gBS = &services;
EFI_RUNTIME_SERVICES *gRT = &runtime;
EFI_GUID gQcomTokenSpaceGuid;
CHAR8 *VabSnapshotMergeStatus[] = {"none", "snapshotted", "merging"};

/* These are explicit EFI/storage/board/UI boundaries. They do no I/O, reboot,
 * flashing, USB access, filesystem mounting, or kernel/device simulation. */
VOID BootStatsSetTimeStamp(UINT32 Tag) { trace("BootStatsSetTimeStamp"); }
VOID DisplayFastbootMenu(VOID) { trace("DisplayFastbootMenu"); }
VOID ExitMenuKeysDetection(VOID) { trace("ExitMenuKeysDetection"); }
VOID InitMultiThreadEnv(VOID) { trace("InitMultiThreadEnv mocked-disabled"); }
BOOLEAN PartitionHasMultiSlot(CONST CHAR16 *Name) { trace("PartitionHasMultiSlot"); return FALSE; }
EFI_STATUS BoardSerialNum(CHAR8 *Serial, UINT32 Size) {
  serial_calls++;
  const char *phase = serial_calls == 1 ? "setup" : "descriptors";
  if (serial_calls == 1 && is("hang-setup")) hang("BoardSerialNum setup");
  if ((serial_calls == 1 && is("setup-serial-error-ignored")) || (serial_calls == 2 && (is("descriptor-serial-error") || is("descriptor-serial-error-superplus")))) {
    trace("BoardSerialNum %s injected-error", phase);
    return EFI_NOT_FOUND;
  }
  trace(serial_calls == 1 ? "BoardSerialNum setup" : "BuildDefaultDescriptors serial");
  snprintf(Serial, Size, "1234abcd");
  return EFI_SUCCESS;
}
BOOLEAN IsDynamicPartitionSupport(VOID) { return FALSE; }
BOOLEAN IsVirtualAbOtaSupported(VOID) { return FALSE; }
VirtualAbMergeStatus GetSnapshotMergeStatus(VOID) { return NONE_MERGE_STATUS; }
BOOLEAN IsSecureBootEnabled(VOID) { return FALSE; }
VOID FindPtnActiveSlot(VOID) { assert(!"Multislot was disabled by boundary mock"); }
VOID PopulateMultislotMetadata(VOID) { assert(!"Multislot was disabled by boundary mock"); }
VOID GetPartitionCount(UINT32 *Count) { trace("GetPartitionCount"); *Count = 0; }
EFI_STATUS PublishGetVarPartitionInfo(struct GetVarPartitionInfo *Info, UINT32 Count) { trace("PublishGetVarPartitionInfo count=%u", Count); return EFI_SUCCESS; }
VOID BoardHwPlatformName(CHAR8 *Name, UINT32 Size) { trace("BoardHwPlatformName"); snprintf(Name, Size, "host"); }
VOID GetRootDeviceType(CHAR8 *Name, UINT32 Size) { snprintf(Name, Size, "UFS"); }
VOID GetPageSize(UINT32 *Size) { *Size = 4096; }
MemCardType CheckRootDeviceType(VOID) { return UFS; }
VOID GetDevInfo(DeviceInfo **Info) { trace("GetDevInfo"); *Info = &devinfo; }
BOOLEAN TargetBatterySocOk(UINT32 *Voltage) { trace("TargetBatterySocOk"); *Voltage = 4000; return TRUE; }
BOOLEAN IsChargingScreenEnable(VOID) { return FALSE; }
BOOLEAN IsUnlocked(VOID) { return TRUE; }
UINT32 BoardPlatformChipVersion(VOID) { return 1; }
BOOLEAN IsDisableParallelDownloadFlash(VOID) { return TRUE; }
static EFI_STATUS EFIAPI read_blocks(EFI_BLOCK_IO_PROTOCOL *This, UINT32 Id, EFI_LBA Lba, UINTN Size, VOID *Buffer) {
  trace("BlockIo.ReadBlocks frp");
  memset(Buffer, 0, Size);
  ((UINT8 *)Buffer)[Size - 1] = 1;
  return EFI_SUCCESS;
}
EFI_STATUS PartitionGetInfo(CHAR16 *Name, EFI_BLOCK_IO_PROTOCOL **BlockIo, EFI_HANDLE **Handle) {
  trace("PartitionGetInfo frp%s", is("setup-frp-error-ignored") ? " injected-error" : "");
  if (is("setup-frp-error-ignored")) return EFI_NOT_FOUND;
  media.BlockSize = 4096;
  media.LastBlock = 1;
  disk.Media = &media;
  disk.ReadBlocks = read_blocks;
  *BlockIo = &disk;
  *Handle = NULL;
  return EFI_SUCCESS;
}
VOID DataReady(UINT64 Size, VOID *Data) {
  trace("DataReady bytes=%lu buffer=%s", Size, Data == GetFastbootDeviceData()->gRxBuffer ? "rx" : Data == FastbootDloadBuffer() ? "download" : "invalid");
  assert(Data == GetFastbootDeviceData()->gRxBuffer || Data == FastbootDloadBuffer());
}
#define DEFINE_COMMAND(Name) VOID Name(CONST CHAR8 *Arg, VOID *Data, UINT32 Size) { assert(!"Command execution is outside this initialization harness"); }
COMMANDS(DEFINE_COMMAND)

UINTN AsciiSPrint(CHAR8 *Buffer, UINTN Size, CONST CHAR8 *Format, ...) {
  char native[128];
  size_t j = 0;
  for (size_t i = 0; Format[i]; i++) {
    assert(j + 2 < sizeof(native));
    native[j++] = Format[i];
    if (Format[i] == '%' && Format[i + 1] == 'a') {
      native[j++] = 's';
      i++;
    }
  }
  native[j] = '\0';
  va_list args;
  va_start(args, Format);
  int result = vsnprintf(Buffer, Size, native, args);
  va_end(args);
  return result < 0 ? 0 : (UINTN)result;
}

int main(int argc, char **argv) {
  assert(argc == 2);
  scenario = argv[1];
  setvbuf(stdout, NULL, _IONBF, 0);
  trace("CASE %s", scenario);
  trace("ABI native-pointer=%zu protocol-size=%zu AllocateTransferBuffer-offset=%zu StartEx-offset=%zu", sizeof(VOID *), sizeof(usb), offsetof(EFI_USB_DEVICE_PROTOCOL, AllocateTransferBuffer), offsetof(EFI_USB_DEVICE_PROTOCOL, StartEx));
  EFI_STATUS status = FastbootInitialize();
  const char *result = status == EFI_SUCCESS ? "success" : status == EFI_DEVICE_ERROR ? "device-error" : status == EFI_NOT_FOUND ? "not-found" : status == EFI_OUT_OF_RESOURCES ? "out-of-resources" : status == EFI_NOT_STARTED ? "not-started" : status == EFI_INVALID_PARAMETER ? "invalid-parameter" : "unknown";
  trace("RESULT %s status=0x%lx", result, status);
  unsigned residual_transfer = 0, residual_pool = 0;
  for (UINTN i = 0; i < allocation_count; i++) {
    if (allocations[i].ptr) {
      if (allocations[i].transfer) residual_transfer++; else residual_pool++;
      free(allocations[i].ptr);
    }
  }
  trace("HOST_CLEANUP residual-transfer=%u residual-pool=%u (source leftovers reclaimed by harness)", residual_transfer, residual_pool);
  return 0;
}
