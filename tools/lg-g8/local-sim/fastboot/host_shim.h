#ifndef HOST_SHIM_H
#define HOST_SHIM_H
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <unistd.h>
#undef NULL
#include <Uefi.h>
#include <Protocol/EFIUsbDevice.h>
#include <Protocol/EFIUsbfnIo.h>
#include <Protocol/BlockIo.h>

/* Only boundary-owned device data is synthetic; protocol structures above are
 * the current checkout's real UEFI and Qualcomm headers. */
typedef UINT64 TimeDuration;
typedef struct { CHAR8 bootloader_version[96]; CHAR8 radio_version[96]; } DeviceInfo;
typedef enum { UNKNOWN, EMMC, UFS, NAND } MemCardType;
typedef enum { NONE_MERGE_STATUS, SNAPSHOTTED, MERGING } VirtualAbMergeStatus;
#define MAX_NUM_PARTITIONS 128
#include "FastbootCmds.host.h"
#include "FastbootMain.host.h"

extern EFI_BOOT_SERVICES *gBS;
extern EFI_RUNTIME_SERVICES *gRT;
extern EFI_GUID gQcomTokenSpaceGuid;
extern EFI_USB_DEVICE_QUALIFIER_DESCRIPTOR DeviceQualifier;
extern EFI_USB_STRING_DESCRIPTOR *StrDescriptors[5];
#define DEBUG(Expression) ((void)0)
#define BS_BL_START 0
#define CHECK_ADD64(A,B) ((UINT64)(A) > UINT64_MAX - (UINT64)(B))
#define LOCAL_ROUND_TO_PAGE(A,B) (((A) + (B) - 1) & ~((UINT64)(B) - 1))
#define FixedPcdGetPtr(Token) ((VOID *)"host-sim-lg-alpha")
#define USBLB_BULK_EP 1
#define ENDPOINT_ADDR(Index, Tx) ((Index) | ((Tx) ? 0x80 : 0))
#define AsciiStrLen(Text) strlen(Text)

VOID *AllocateZeroPool(UINTN Size);
VOID FreePool(VOID *Buffer);
UINTN AsciiSPrint(CHAR8 *Buffer, UINTN Size, CONST CHAR8 *Format, ...);
VOID BootStatsSetTimeStamp(UINT32 Tag);
VOID DisplayFastbootMenu(VOID);
VOID ExitMenuKeysDetection(VOID);
VOID InitMultiThreadEnv(VOID);
VOID BuildDefaultDescriptors(USB_DEVICE_DESCRIPTOR **DevDesc, VOID **Descriptors,
                             USB_DEVICE_DESCRIPTOR **SSDevDesc, VOID **SSDescriptors);
BOOLEAN PartitionHasMultiSlot(CONST CHAR16 *Name);
EFI_STATUS BoardSerialNum(CHAR8 *Serial, UINT32 Size);
BOOLEAN IsDynamicPartitionSupport(VOID);
BOOLEAN IsVirtualAbOtaSupported(VOID);
VirtualAbMergeStatus GetSnapshotMergeStatus(VOID);
extern CHAR8 *VabSnapshotMergeStatus[];
BOOLEAN IsSecureBootEnabled(VOID);
VOID FindPtnActiveSlot(VOID);
VOID PopulateMultislotMetadata(VOID);
VOID GetPartitionCount(UINT32 *Count);
EFI_STATUS PublishGetVarPartitionInfo(struct GetVarPartitionInfo *Info, UINT32 Count);
VOID BoardHwPlatformName(CHAR8 *Name, UINT32 Size);
VOID GetRootDeviceType(CHAR8 *Name, UINT32 Size);
VOID GetPageSize(UINT32 *Size);
MemCardType CheckRootDeviceType(VOID);
BOOLEAN TargetBatterySocOk(UINT32 *Voltage);
BOOLEAN IsChargingScreenEnable(VOID);
BOOLEAN IsUnlocked(VOID);
UINT32 BoardPlatformChipVersion(VOID);
EFI_STATUS PartitionGetInfo(CHAR16 *Name, EFI_BLOCK_IO_PROTOCOL **BlockIo, EFI_HANDLE **Handle);

#define COMMANDS(X) \
  X(CmdOemEnableChargerScreen) X(CmdOemDisableChargerScreen) X(CmdOemOffModeCharger) \
  X(CmdOemSelectDisplayPanel) X(CmdOemDevinfo) X(CmdContinue) X(CmdReboot) \
  X(CmdRebootBootloader) X(CmdGetVar) X(CmdDownload) X(CmdOemDisplayCommandLine)
#define DECLARE_COMMAND(Name) VOID Name(CONST CHAR8 *Arg, VOID *Data, UINT32 Size);
COMMANDS(DECLARE_COMMAND)
#undef DECLARE_COMMAND
#endif
