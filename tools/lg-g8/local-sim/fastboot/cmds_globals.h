/* Linkage scaffolding for exact extracted FastbootCmds functions. No init
 * decisions, memory sizing, sequencing, or return handling are replaced here. */
STATIC FASTBOOT_VAR *Varlist;
STATIC FASTBOOT_CMD *cmdlist;
STATIC BOOLEAN Finished;
STATIC UINT8 *mDataBuffer, *mUsbDataBuffer, *mFlashDataBuffer;
STATIC UINT64 MaxDownLoadSize, mNumDataBytes, mFlashNumDataBytes, mBytesReceivedSoFar;
STATIC ANDROID_FASTBOOT_STATE mState = ExpectCmdState;
STATIC UINT32 IsAllowUnlock;
STATIC CHAR8 StrSerialNum[MAX_RSP_SIZE], FullProduct[MAX_RSP_SIZE], StrVariant[MAX_RSP_SIZE];
STATIC CHAR8 StrBatteryVoltage[MAX_RSP_SIZE], StrBatterySocOk[MAX_RSP_SIZE];
STATIC CHAR8 ChargeScreenEnable[MAX_RSP_SIZE], OffModeCharge[MAX_RSP_SIZE], StrSocVersion[MAX_RSP_SIZE];
STATIC CHAR8 LogicalBlkSizeStr[MAX_RSP_SIZE], EraseBlkSizeStr[MAX_RSP_SIZE];
STATIC CHAR8 MaxDownloadSizeStr[MAX_RSP_SIZE], SnapshotMergeState[MAX_RSP_SIZE];
STATIC struct GetVarPartitionInfo PublishedPartInfo[MAX_NUM_PARTITIONS];
#define NAND_PAGES_PER_BLOCK 64
STATIC EFI_STATUS FastbootCommandSetup(VOID *Base, UINT64 Size);

/* Explicit host controls avoid replacing the source's event loop. */
VOID HostSetDownloadState(BOOLEAN IsData) { mState = IsData ? ExpectDataState : ExpectCmdState; }
VOID HostSetFinished(VOID) { Finished = TRUE; }
