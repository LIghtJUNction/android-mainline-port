/* SPDX-License-Identifier: BSD-2-Clause-Patent
 * A small standard EFI launcher for local QEMU tests. No Qualcomm protocols.
 */
#include <Uefi.h>
#include <Protocol/DevicePath.h>
#include <Protocol/LoadedImage.h>

static EFI_GUID loaded_guid = EFI_LOADED_IMAGE_PROTOCOL_GUID;
static EFI_GUID path_guid = EFI_DEVICE_PATH_PROTOCOL_GUID;
static CHAR16 kernel_name[] = L"\\kernel.efi";
static CHAR16 command_line[] =
    L"console=ttyAMA0,115200 earlycon=pl011,0x09000000 acpi=off "
    L"rdinit=/sim-init initrd=\\initramfs.cpio panic=0 loglevel=7";

static void copy_bytes(void *to, const void *from, UINTN size)
{
    UINT8 *d = to;
    const UINT8 *s = from;
    while (size--)
        *d++ = *s++;
}

static void report_status(EFI_SYSTEM_TABLE *st, CHAR16 *message, EFI_STATUS status)
{
    CHAR16 value[19] = L"0x0000000000000000";
    static const CHAR16 digits[] = L"0123456789abcdef";
    for (UINTN i = 0; i < 16; ++i)
        value[2 + i] = digits[(status >> ((15 - i) * 4)) & 15];
    /* Keep the numeric status terminated separately from CR/LF. */
    value[18] = 0;
    st->ConOut->OutputString(st->ConOut, message);
    st->ConOut->OutputString(st->ConOut, value);
    st->ConOut->OutputString(st->ConOut, L"\r\n");
}

EFI_STATUS EFIAPI EfiMain(EFI_HANDLE image, EFI_SYSTEM_TABLE *st)
{
    EFI_BOOT_SERVICES *bs = st->BootServices;
    EFI_LOADED_IMAGE_PROTOCOL *self, *kernel;
    EFI_DEVICE_PATH_PROTOCOL *volume_path, *node, *file_path;
    EFI_HANDLE child = NULL;
    EFI_STATUS status;
    UINTN prefix = 0, path_bytes, name_bytes = sizeof(kernel_name);

    st->ConOut->OutputString(st->ConOut, L"LOCAL-SIM EFI_LOADER_ENTER\r\n");
    status = bs->HandleProtocol(image, &loaded_guid, (VOID **)&self);
    if (EFI_ERROR(status)) {
        report_status(st, L"LOCAL-SIM SELF_PROTOCOL_FAILED ", status);
        return status;
    }
    status = bs->HandleProtocol(self->DeviceHandle, &path_guid, (VOID **)&volume_path);
    if (EFI_ERROR(status)) {
        report_status(st, L"LOCAL-SIM VOLUME_PATH_FAILED ", status);
        return status;
    }
    node = volume_path;
    while (node->Type != END_DEVICE_PATH_TYPE) {
        UINTN size = node->Length[0] | ((UINTN)node->Length[1] << 8);
        if (size < sizeof(*node) || prefix + size > 4096)
            return EFI_COMPROMISED_DATA;
        prefix += size;
        node = (EFI_DEVICE_PATH_PROTOCOL *)((UINT8 *)node + size);
    }
    path_bytes = prefix + sizeof(*node) + name_bytes + sizeof(*node);
    status = bs->AllocatePool(EfiLoaderData, path_bytes, (VOID **)&file_path);
    if (EFI_ERROR(status))
        return status;
    copy_bytes(file_path, volume_path, prefix);
    node = (EFI_DEVICE_PATH_PROTOCOL *)((UINT8 *)file_path + prefix);
    node->Type = MEDIA_DEVICE_PATH;
    node->SubType = MEDIA_FILEPATH_DP;
    node->Length[0] = (sizeof(*node) + name_bytes) & 255;
    node->Length[1] = (sizeof(*node) + name_bytes) >> 8;
    copy_bytes(node + 1, kernel_name, name_bytes);
    node = (EFI_DEVICE_PATH_PROTOCOL *)((UINT8 *)node + sizeof(*node) + name_bytes);
    node->Type = END_DEVICE_PATH_TYPE;
    node->SubType = END_ENTIRE_DEVICE_PATH_SUBTYPE;
    node->Length[0] = sizeof(*node);
    node->Length[1] = 0;

    status = bs->LoadImage(FALSE, image, file_path, NULL, 0, &child);
    bs->FreePool(file_path);
    if (EFI_ERROR(status)) {
        report_status(st, L"LOCAL-SIM KERNEL_LOAD_FAILED ", status);
        return status;
    }
    status = bs->HandleProtocol(child, &loaded_guid, (VOID **)&kernel);
    if (EFI_ERROR(status))
        return status;
    kernel->LoadOptions = command_line;
    kernel->LoadOptionsSize = sizeof(command_line);
    st->ConOut->OutputString(st->ConOut, L"LOCAL-SIM EFI_KERNEL_LOAD_OK\r\n");
    status = bs->StartImage(child, NULL, NULL);
    report_status(st, L"LOCAL-SIM KERNEL_RETURNED ", status);
    return status;
}
