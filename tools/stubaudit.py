#!/usr/bin/env python3
"""Static audit of PS2Recomp's runtime SDK handlers for EE argument-passing mistakes.

The EE ABI passes integer arguments 1-8 in a0-a3, t0-t3 (regs 4-11) and arguments 9 and up
on the stack in 8-byte slots starting at sp+0. A handler that reads arguments 5-8 from
sp+16..28 (the O32 layout) is reading the caller's locals. This tool lists, for every
handler, each stack read (with its offset) and each read of regs 8-11, follows helper calls
(including delegation to ps2_syscalls::X), and flags mismatches against a known signature.

Handlers scanned (read-only): ps2xRuntime/src/lib/Kernel/Stubs/*.cpp (stubs) and the
handlers in Kernel/Syscalls/*.cpp that PS2_SYSCALL_LIST names. A bound name resolves to the
syscall handler first (game_overrides.cpp / ps2_recompiler.cpp), so a stub whose name is
also a syscall is shadowed and never runs for a binding; the tool marks that.

Signature sources, in order of preference, each reported with the result:
  declared - the handler is declared through the typed stub ABI layer (ps2_stub_abi.h:
             PS2_STUB / PS2_STUB_TYPED + PS2_STUB_DISPATCH / PS2_STUB_ALIAS): its C signature
             is the decoding itself. The row's reads are what the typed wrapper decodes plus the
             impl body's own reads; a transition handler's old decoding (NAME_legacy) is folded
             in as legacy_reads, with LEGACYDIFF (info) when it reads a location the
             declaration does not decode, IMPLREG (info) when the impl body reads an argument
             register itself, SIGDIFF (info) when the builtin table disagrees. A declaration
             marked PS2_STUB_DEVIATION(name, "why") deviates from the SDK prototype on purpose
             (kept to preserve the old decoding): DEVIATION (info) lists it, and a builtin-table
             disagreement is reported under it instead of as SIGDIFF.
  comment  - a prototype NAME(a, b, ...) in a comment inside or just above the handler
  builtin  - the table SDK_SIGS below, typed in from the Sony SDK 3.x library reference
             (no SDK headers are vendored in the repo; verify against the game's decompile)
  symdb    - the analyzer's SCE symbol database only names the library a symbol belongs to
             (it has no argument counts); shown as context, never used for counts
Argument counts are integer-class (GPR) arguments only: floats go in f12-f19 separately.

Usage:
    python tools/stubaudit.py [--bound local/recomp/srwz.toml] [--json] [--all]
                              [--root <PS2Recomp tree>] [--signatures <ps2x_stubsigs json>]
    python tools/stubaudit.py --selftest
--root scans another PS2Recomp checkout (a worktree under review) instead of the submodule.
--signatures cross-checks the declarations against the build's own dump (ps2x_stubsigs):
DUMPDIFF (severe) when a handler is missing on either side, its argument layout differs, or
one side marks a deviation the other does not.
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def set_root(ps2recomp):
    """Point the scan at a PS2Recomp tree (default: the submodule)."""
    global RT, KERNEL, CALL_LIST, SYMDB
    RT = os.path.join(ps2recomp, "ps2xRuntime")
    KERNEL = os.path.join(RT, "src", "lib", "Kernel")
    CALL_LIST = os.path.join(RT, "include", "ps2_call_list.h")
    SYMDB = os.path.join(ps2recomp, "ps2xAnalyzer", "include", "ps2recomp", "sce_symbol_database_data.h")


set_root(os.path.join(ROOT, "third_party", "PS2Recomp"))

# name: (integer-class argument count, prototype). Variadic entries give the largest count
# the library uses. Typed in from the SDK reference: verify against the game's own copy.
SDK_SIGS = {
    # libgraph
    "sceGsResetGraph": (4, "short mode, short inter, short omode, short ffmode"),
    "sceGsSetDefDispEnv": (6, "sceGsDispEnv *disp, short psm, short w, short h, short dx, short dy"),
    "sceGsSetDefDrawEnv": (6, "sceGsDrawEnv1 *draw, short psm, short w, short h, short ztest, short zpsm"),
    "sceGsSetDefDrawEnv2": (6, "sceGsDrawEnv2 *draw, short psm, short w, short h, short ztest, short zpsm"),
    "sceGsSetDefDBuff": (7, "sceGsDBuff *db, short psm, short w, short h, short ztest, short zpsm, short clear"),
    "sceGsSetDefDBuffDc": (7, "sceGsDBuffDc *db, short psm, short w, short h, short ztest, short zpsm, short clear"),
    "sceGsSetDefClear": (11, "sceGsClear *cp, short ztest, short x, short y, short w, short h, u_char r, u_char g, u_char b, u_char a, u_int z"),
    "sceGsSetDefLoadImage": (8, "sceGsLoadImage *lp, short dbp, short dbw, short dpsm, short x, short y, short w, short h"),
    "sceGsSetDefStoreImage": (8, "sceGsStoreImage *sp, short sbp, short sbw, short spsm, short x, short y, short w, short h"),
    "sceGsExecLoadImage": (2, "sceGsLoadImage *lp, u_long128 *srcaddr"),
    "sceGsExecStoreImage": (2, "sceGsStoreImage *sp, u_long128 *dstaddr"),
    "sceGsPutDispEnv": (1, "sceGsDispEnv *disp"),
    "sceGsPutDrawEnv": (1, "sceGifTag *giftag"),
    "sceGsSwapDBuff": (2, "sceGsDBuff *db, int id"),
    "sceGsSwapDBuffDc": (2, "sceGsDBuffDc *db, int id"),
    "sceGsSyncV": (1, "int mode"),
    "sceGsSyncPath": (2, "int mode, u_short timeout"),
    "sceGsSyncVCallback": (1, "int (*func)(int)"),
    "sceGsGetGParam": (0, ""),
    "sceGsResetPath": (0, ""),
    # libpkt
    "sceGifPkInit": (2, "sceGifPacket *pkt, u_long128 *buf"),
    "sceGifPkReset": (1, "sceGifPacket *pkt"),
    "sceGifPkTerminate": (1, "sceGifPacket *pkt"),
    "sceGifPkCnt": (4, "sceGifPacket *pkt, u_int opt1, u_int opt2, u_int flag"),
    "sceGifPkEnd": (4, "sceGifPacket *pkt, u_int opt1, u_int opt2, u_int flag"),
    "sceGifPkRef": (6, "sceGifPacket *pkt, u_long128 *addr, u_int size, u_int opt1, u_int opt2, u_int flag"),
    "sceGifPkOpenGifTag": (2, "sceGifPacket *pkt, u_long128 gifTag"),
    "sceGifPkCloseGifTag": (1, "sceGifPacket *pkt"),
    "sceGifPkAddGsAD": (3, "sceGifPacket *pkt, int addr, u_long data"),
    "sceGifPkAddGsData": (2, "sceGifPacket *pkt, u_long data"),
    "sceGifPkReserve": (2, "sceGifPacket *pkt, u_int count"),
    "sceGifPkRefLoadImage": (10, "sceGifPacket *pkt, u_short bp, u_char psm, u_short bw, u_long128 *image, u_int size, u_int x, u_int y, u_int w, u_int h"),
    "sceVif1PkInit": (2, "sceVif1Packet *pkt, u_long128 *buf"),
    "sceVif1PkReset": (1, "sceVif1Packet *pkt"),
    "sceVif1PkTerminate": (1, "sceVif1Packet *pkt"),
    "sceVif1PkCnt": (2, "sceVif1Packet *pkt, u_int flag"),
    "sceVif1PkEnd": (2, "sceVif1Packet *pkt, u_int flag"),
    "sceVif1PkCall": (3, "sceVif1Packet *pkt, u_long128 *addr, u_int flag"),
    "sceVif1PkOpenDirectCode": (2, "sceVif1Packet *pkt, int i"),
    "sceVif1PkCloseDirectCode": (1, "sceVif1Packet *pkt"),
    "sceVif1PkOpenGifTag": (2, "sceVif1Packet *pkt, u_long128 gifTag"),
    "sceVif1PkCloseGifTag": (1, "sceVif1Packet *pkt"),
    "sceVif1PkAddGsAD": (3, "sceVif1Packet *pkt, int addr, u_long data"),
    "sceVif1PkReserve": (2, "sceVif1Packet *pkt, u_int count"),
    "sceVif1PkAlign": (3, "sceVif1Packet *pkt, int bit, int pos"),
    # libpad
    "scePadInit": (1, "int mode"),
    "scePadInit2": (1, "int mode"),
    "scePadEnd": (0, ""),
    "scePadPortOpen": (3, "int port, int slot, u_long128 *addr"),
    "scePadPortClose": (2, "int port, int slot"),
    "scePadRead": (3, "int port, int slot, u_char *rdata"),
    "scePadGetState": (2, "int port, int slot"),
    "scePadGetReqState": (2, "int port, int slot"),
    "scePadSetReqState": (3, "int port, int slot, int state"),
    "scePadInfoAct": (4, "int port, int slot, int actno, int term"),
    "scePadInfoComb": (4, "int port, int slot, int listno, int offs"),
    "scePadInfoMode": (4, "int port, int slot, int term, int offs"),
    "scePadSetMainMode": (4, "int port, int slot, int offs, int lock"),
    "scePadSetActDirect": (3, "int port, int slot, const u_char *data"),
    "scePadSetActAlign": (3, "int port, int slot, const u_char *data"),
    "scePadInfoPressMode": (2, "int port, int slot"),
    "scePadEnterPressMode": (2, "int port, int slot"),
    "scePadExitPressMode": (2, "int port, int slot"),
    "scePadGetButtonMask": (2, "int port, int slot"),
    "scePadSetButtonInfo": (3, "int port, int slot, int info"),
    "scePadSetVrefParam": (3, "int port, int slot, u_char *param"),
    "scePadGetFrameCount": (2, "int port, int slot"),
    "scePadGetPortMax": (0, ""),
    "scePadGetSlotMax": (1, "int port"),
    "scePadGetModVersion": (0, ""),
    "scePadSetWarningLevel": (1, "int level"),
    "scePadStateIntToStr": (2, "int state, unsigned char *str"),
    "scePadReqIntToStr": (2, "int state, unsigned char *str"),
    # libmc
    "sceMcInit": (0, ""),
    "sceMcEnd": (0, ""),
    "sceMcGetInfo": (5, "int port, int slot, int *type, int *free, int *format"),
    "sceMcOpen": (4, "int port, int slot, const char *name, int mode"),
    "sceMcClose": (1, "int fd"),
    "sceMcRead": (3, "int fd, void *buff, int size"),
    "sceMcWrite": (3, "int fd, const void *buff, int size"),
    "sceMcSeek": (3, "int fd, int offset, int mode"),
    "sceMcFlush": (1, "int fd"),
    "sceMcMkdir": (3, "int port, int slot, const char *name"),
    "sceMcChdir": (4, "int port, int slot, const char *path, char *pwd"),
    "sceMcGetDir": (6, "int port, int slot, const char *name, unsigned mode, int maxent, sceMcTblGetDir *table"),
    "sceMcSetFileInfo": (5, "int port, int slot, const char *name, const char *info, unsigned valid"),
    "sceMcDelete": (3, "int port, int slot, const char *name"),
    "sceMcFormat": (2, "int port, int slot"),
    "sceMcUnformat": (2, "int port, int slot"),
    "sceMcGetEntSpace": (3, "int port, int slot, const char *path"),
    "sceMcRename": (4, "int port, int slot, const char *org, const char *new"),
    "sceMcSync": (3, "int mode, int *cmd, int *result"),
    "sceMcGetSlotMax": (1, "int port"),
    "sceMcChangeThreadPriority": (1, "int level"),
    # libcdvd
    "sceCdInit": (1, "int mode"),
    "sceCdMmode": (1, "int media"),
    "sceCdRead": (4, "u_int lsn, u_int sectors, void *buf, sceCdRMode *mode"),
    "sceCdReadIOPm": (4, "u_int lsn, u_int sectors, void *buf, sceCdRMode *mode"),
    "sceCdSync": (1, "int mode"),
    "sceCdSyncS": (1, "int mode"),
    "sceCdDiskReady": (1, "int mode"),
    "sceCdGetError": (0, ""),
    "sceCdCallback": (1, "void (*func)(int)"),
    "sceCdInitEeCB": (3, "int prio, void *stack, int size"),
    "sceCdSearchFile": (2, "sceCdlFILE *fp, const char *name"),
    "sceCdReadChain": (2, "sceCdRChain *tag, sceCdRMode *mode"),
    "sceCdGetToc": (1, "u_char *toc"),
    "sceCdSeek": (1, "u_int lsn"),
    "sceCdTrayReq": (2, "int mode, u_int *traycnt"),
    "sceCdReadClock": (1, "sceCdCLOCK *rtc"),
    "sceCdRI": (2, "u_char *buf, u_int *result"),
    "sceCdRM": (2, "char *m, u_int *stat"),
    "sceCdApplyNCmd": (3, "u_char cmd, const void *inBuff, u_short inBuffSize"),
    "sceCdIntToPos": (2, "int i, sceCdlLOCCD *p"),
    "sceCdPosToInt": (1, "sceCdlLOCCD *p"),
    "sceCdChangeThreadPriority": (1, "int prio"),
    "sceCdStInit": (3, "u_int bufmax, u_int bankmax, u_int iop_bufaddr"),
    "sceCdStRead": (4, "u_int size, u_int *buf, u_int mode, u_int *err"),
    "sceCdStStart": (2, "u_int lsn, sceCdRMode *mode"),
    "sceCdStSeek": (1, "u_int lsn"),
    "sceCdStSeekF": (1, "u_int lsn"),
    "sceCdStream": (5, "u_int lsn, u_int nsectors, void *buf, CdvdStCmd_t cmd, sceCdRMode *mode"),
    # sifcmd / sifrpc / sifdev / iopheap
    "sceSifSendCmd": (6, "u_int cid, void *packet, u_int packet_size, void *src_extra, void *dest_extra, int size_extra"),
    "isceSifSendCmd": (6, "u_int cid, void *packet, u_int packet_size, void *src_extra, void *dest_extra, int size_extra"),
    "sceSifCallRpc": (9, "sceSifClientData *cd, u_int fno, u_int mode, void *send, int ssize, void *receive, int rsize, sceSifEndFunc end_func, void *end_para"),
    "SifCallRpc": (9, "sceSifClientData *cd, u_int fno, u_int mode, void *send, int ssize, void *receive, int rsize, sceSifEndFunc end_func, void *end_para"),
    "sceSifRegisterRpc": (7, "sceSifServeData *sd, u_int command, sceSifRpcFunc func, void *buff, sceSifRpcFunc cfunc, void *cbuff, sceSifQueueData *qd"),
    "SifRegisterRpc": (7, "sceSifServeData *sd, u_int command, sceSifRpcFunc func, void *buff, sceSifRpcFunc cfunc, void *cbuff, sceSifQueueData *qd"),
    "sceSifGetOtherData": (5, "sceSifReceiveData *rd, void *src, void *dest, int size, u_int mode"),
    "sceSifBindRpc": (3, "sceSifClientData *cd, u_int request, u_int mode"),
    "SifBindRpc": (3, "sceSifClientData *cd, u_int request, u_int mode"),
    "sceSifCheckStatRpc": (1, "sceSifRpcData *cd"),
    "sceSifSetRpcQueue": (2, "sceSifQueueData *qd, int thread_id"),
    "sceSifRemoveRpc": (2, "sceSifServeData *sd, sceSifQueueData *qd"),
    "sceSifRemoveRpcQueue": (1, "sceSifQueueData *qd"),
    "sceSifRpcLoop": (1, "sceSifQueueData *qd"),
    "sceSifGetNextRequest": (1, "sceSifQueueData *qd"),
    "sceSifExecRequest": (1, "sceSifServeData *sd"),
    "sceSifInitRpc": (1, "u_int mode"),
    "sceSifInitCmd": (0, ""),
    "sceSifExitCmd": (0, ""),
    "sceSifExitRpc": (0, ""),
    "sceSifAddCmdHandler": (3, "u_int pos, sceSifCmdHandler f, void *data"),
    "sceSifRemoveCmdHandler": (1, "u_int pos"),
    "sceSifSetCmdBuffer": (2, "sceSifCmdData *db, int size"),
    "sceSifSetSysCmdBuffer": (2, "sceSifCmdData *db, int size"),
    "sceSifSetDma": (2, "sceSifDmaData *sdd, int len"),
    "isceSifSetDma": (2, "sceSifDmaData *sdd, int len"),
    "sceSifSetDChain": (0, ""),
    "isceSifSetDChain": (0, ""),
    "sceSifDmaStat": (1, "u_int id"),
    "sceSifGetReg": (1, "u_int reg"),
    "sceSifSetReg": (2, "u_int reg, u_int val"),
    "sceSifGetSreg": (1, "int reg"),
    "sceSifSetSreg": (2, "int reg, u_int val"),
    "sceSifLoadModule": (3, "const char *filename, int args, const char *argp"),
    "sceSifLoadModuleBuffer": (3, "const void *addr, int args, const char *argp"),
    "sceSifLoadElf": (2, "const char *name, sceExecData *data"),
    "sceSifLoadElfPart": (3, "const char *name, const char *section, sceExecData *data"),
    "sceSifLoadFileReset": (0, ""),
    "sceSifAllocIopHeap": (1, "int size"),
    "sceSifFreeIopHeap": (1, "void *addr"),
    "sceSifLoadIopHeap": (2, "const char *path, void *addr"),
    "sceSifInitIopHeap": (0, ""),
    "sceSifAllocSysMemory": (3, "int type, int size, void *addr"),
    "sceSifFreeSysMemory": (1, "void *addr"),
    "sceSifRebootIop": (1, "const char *img"),
    "sceSifSyncIop": (0, ""),
    "sceSifIsAliveIop": (0, ""),
    "sceSifSetIopAddr": (2, "u_int addr, u_int val"),
    "sceSifGetIopAddr": (1, "u_int addr"),
    "sceSifWriteBackDCache": (2, "void *ptr, int size"),
    # libdma
    "sceDmaReset": (1, "int mode"),
    "sceDmaGetChan": (1, "int id"),
    "sceDmaPutEnv": (1, "sceDmaEnv *env"),
    "sceDmaGetEnv": (1, "sceDmaEnv *env"),
    "sceDmaSend": (2, "sceDmaChan *d, void *tag"),
    "sceDmaSendN": (3, "sceDmaChan *d, void *addr, int size"),
    "sceDmaSendI": (3, "sceDmaChan *d, void *addr, int size"),
    "sceDmaRecv": (1, "sceDmaChan *d"),
    "sceDmaRecvN": (3, "sceDmaChan *d, void *addr, int size"),
    "sceDmaRecvI": (3, "sceDmaChan *d, void *addr, int size"),
    "sceDmaSync": (3, "sceDmaChan *d, int mode, int timeout"),
    "sceDmaWatch": (4, "sceDmaChan *d, void *addr, int mode, int timeout"),
    "sceDmaPause": (1, "sceDmaChan *d"),
    "sceDmaRestart": (2, "sceDmaChan *d, void *p"),
    # libipu
    "sceIpuInit": (0, ""),
    "sceIpuRestartDMA": (1, "sceIpuDmaEnv *env"),
    "sceIpuStopDMA": (1, "sceIpuDmaEnv *env"),
    "sceIpuSync": (2, "int mode, u_short timeout"),
    # libmpeg
    "sceMpegInit": (0, ""),
    "sceMpegCreate": (3, "sceMpeg *mp, u_char *work_area, int work_area_size"),
    "sceMpegDelete": (1, "sceMpeg *mp"),
    "sceMpegReset": (1, "sceMpeg *mp"),
    "sceMpegAddBs": (3, "sceMpeg *mp, u_long128 *bs, int size"),
    "sceMpegGetPicture": (3, "sceMpeg *mp, sceIpuRGB32 *rgb32, int mbcount"),
    "sceMpegGetPictureRAW8": (3, "sceMpeg *mp, sceIpuRAW8 *raw8, int mbcount"),
    "sceMpegAddCallback": (4, "sceMpeg *mp, sceMpegCbType type, sceMpegCallback func, void *data"),
    "sceMpegAddStrCallback": (5, "sceMpeg *mp, sceMpegStrType strType, int ch, sceMpegCallback cb, void *data"),
    "sceMpegDemuxPss": (3, "sceMpeg *mp, u_char *pss, int size"),
    "sceMpegDemuxPssRing": (5, "sceMpeg *mp, u_char *pss, int size, u_char *pss_buff, int pss_buff_size"),
    "sceMpegClearRefBuff": (1, "sceMpeg *mp"),
    "sceMpegSetDecodeMode": (4, "sceMpeg *mp, int ni, int np, int nb"),
    "sceMpegGetDecodeMode": (4, "sceMpeg *mp, int *ni, int *np, int *nb"),
    "sceMpegIsEnd": (1, "sceMpeg *mp"),
    "sceMpegSetImageBuff": (2, "sceMpeg *mp, void *ptr"),
    # libsdr (sceSdRemote is variadic: rSdVoiceTrans / rSdBlockTrans take 7)
    "sceSdRemoteInit": (0, ""),
    "sceSdRemote": (7, "int arg, int command, ... (VoiceTrans/BlockTrans: channel, mode, addr, spu_addr|size, size)"),
    "sceSdTransToIOP": (4, "void *buff, u_int sendAddr, u_int size, u_int isBlock"),
    "sceSdCallBack": (1, "void *func"),
    # deci2
    "sceDeci2Open": (3, "u_short protocol, void *opt, void (*handler)(int, void *)"),
    "sceDeci2Close": (1, "int s"),
    "sceDeci2ReqSend": (2, "int s, char dest"),
    "sceDeci2Poll": (1, "int s"),
    "sceDeci2ExRecv": (3, "int s, void *buf, u_short len"),
    "sceDeci2ExSend": (3, "int s, void *buf, u_short len"),
    "sceDeci2ExReqSend": (2, "int s, char dest"),
    "sceDeci2ExLock": (1, "int s"),
    "sceDeci2ExUnLock": (1, "int s"),
    # libvu0 (integer-class arguments only; floats travel in f12-f19)
    "sceVu0RotTransPersN": (5, "sceVu0IVECTOR *v0, sceVu0FMATRIX m0, sceVu0FVECTOR *v1, int n, int mode"),
    "sceVu0RotTransPers": (4, "sceVu0IVECTOR v0, sceVu0FMATRIX m0, sceVu0FVECTOR v1, int mode"),
    "sceVu0LightColorMatrix": (5, "sceVu0FMATRIX m, sceVu0FVECTOR c0, sceVu0FVECTOR c1, sceVu0FVECTOR c2, sceVu0FVECTOR a"),
    "sceVu0NormalLightMatrix": (4, "sceVu0FMATRIX m, sceVu0FVECTOR l0, sceVu0FVECTOR l1, sceVu0FVECTOR l2"),
    "sceVu0CameraMatrix": (4, "sceVu0FMATRIX m, sceVu0FVECTOR p, sceVu0FVECTOR zd, sceVu0FVECTOR yd"),
    "sceVu0ClipScreen3": (3, "sceVu0FVECTOR v0, sceVu0FVECTOR v1, sceVu0FVECTOR v2"),
    "sceVu0ViewScreenMatrix": (1, "sceVu0FMATRIX m, float scrz, ax, ay, cx, cy, zmin, zmax, nearz, farz", 1),
    "sceVu0DropShadowMatrix": (3, "sceVu0FMATRIX m, sceVu0FVECTOR lp, float a, float b, float c, int mode"),
    # file I/O
    "sceRead": (3, "int fd, void *buf, int nbyte"),
    "sceWrite": (3, "int fd, const void *buf, int nbyte"),
    "sceLseek": (3, "int fd, int offset, int whence"),
    "sceIoctl": (3, "int fd, int req, void *arg"),
    "sceClose": (1, "int fd"),
    "read": (3, "int fd, void *buf, size_t n"),
    "write": (3, "int fd, const void *buf, size_t n"),
    "lseek": (3, "int fd, off_t off, int whence"),
}

READ_REG_RE = re.compile(r"\b(?:getRegU32|getRegS32|getRegU64|GPR_U32|GPR_S32|GPR_U64|GPR_S64|GPR_VEC)\s*\(\s*ctx\s*,\s*(\d+)\s*\)")
CTX_R_RE = re.compile(r"ctx\s*->\s*r\s*\[\s*(\d+)\s*\]")
SP_ASSIGN_RE = re.compile(r"\b(\w+)\s*=\s*getRegU32\s*\(\s*ctx\s*,\s*29\s*\)")
SP_EXPR = r"getRegU32\s*\(\s*ctx\s*,\s*29\s*\)"
NUM_RE = re.compile(r"^\s*(0[xX][0-9a-fA-F]+|\d+)[uUlL]*\s*$")
DEF_RE = re.compile(r"(?m)^[ \t]*(?:(?:static|inline|constexpr|extern)\s+)*[\w:<>\*&]+(?:[ \t]+[\w:<>\*&]+)*[ \t\*&]+(\w+)\s*\(([^;{}]*?)\)\s*(?:const\s*)?(?:noexcept\s*)?\{")
KEYWORDS = {"if", "for", "while", "switch", "catch", "return", "sizeof", "else"}
MEM_READ_FUNCS = r"(?:FAST_READ(?:8|16|32|64|128)|READ(?:8|16|32|64|128)|getConstMemPtr|getMemPtr|tryReadWordFromGuest|readGuestU32|readGuestU64)"


def strip_comments(src):
    """Blank out comments and string contents, keeping offsets and newlines."""
    out = list(src)
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c == '"' or c == "'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out)


def match_brace(text, open_idx):
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text) - 1


def split_args(s):
    args, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        args.append(cur.strip())
    return args


def call_args(text, open_paren):
    depth = 0
    for i in range(open_paren, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return split_args(text[open_paren + 1:i])
    return []


def num(s):
    m = NUM_RE.match(s)
    return int(m.group(1), 0) if m else None


class Func:
    def __init__(self, name, params, path, line, body, raw_body, start, kind):
        self.name, self.params, self.path, self.line = name, params, path, line
        self.body, self.raw_body, self.start, self.kind = body, raw_body, start, kind
        self.regs = {}        # reg -> first line
        self.stack = []       # (offset or None, line, how)
        self.calls = set()
        self.notes = []


def line_of(text, idx, base_line):
    return base_line + text.count("\n", 0, idx)


def parse_functions(path, src, kind_of):
    clean = strip_comments(src)
    funcs = []
    pos = 0
    prev_end = 0
    while True:
        m = DEF_RE.search(clean, pos)
        if not m:
            break
        name = m.group(1)
        brace = m.end() - 1
        end = match_brace(clean, brace)
        if name in KEYWORDS:
            pos = m.end()
            continue
        params = [p.strip() for p in split_args(m.group(2))]
        line = clean.count("\n", 0, m.start(1)) + 1
        f = Func(name, params, path, line, clean[brace:end + 1], src[brace:end + 1], brace, kind_of(name, params))
        f.header_start = m.start()
        f.raw_before = src[max(prev_end, m.start() - 1500):m.start()]
        funcs.append(f)
        pos = end + 1
        prev_end = end + 1
    return funcs


def param_names(params):
    out = []
    for p in params:
        m = re.search(r"(\w+)\s*(?:=.*)?$", p)
        out.append(m.group(1) if m else "")
    return out


def find_stack_helpers(funcs):
    """Functions that read memory at sp + <parameter>: name -> (offset param idx, sp param idx or None)."""
    helpers = {}
    for f in funcs:
        names = param_names(f.params)
        spvars = set(SP_ASSIGN_RE.findall(f.body))
        sp_params = [i for i, n in enumerate(names) if n in ("sp", "stackPointer")]
        for i, pn in enumerate(names):
            if not pn or pn in ("rdram", "ctx", "runtime"):
                continue
            ctx_pats = [SP_EXPR + r"\s*\+\s*" + pn + r"\b"] + [r"\b" + v + r"\s*\+\s*" + pn + r"\b" for v in spvars]
            if any(re.search(p, f.body) for p in ctx_pats):
                helpers.setdefault(f.name, []).append((i, None, f))
                continue
            for j in sp_params:
                if re.search(r"\b" + names[j] + r"\s*\+\s*" + pn + r"\b", f.body):
                    helpers.setdefault(f.name, []).append((i, j, f))
    return helpers


def scan_body(f, helpers, funcnames, vararg):
    body = f.body
    for m in READ_REG_RE.finditer(body):
        r = int(m.group(1))
        f.regs.setdefault(r, line_of(body, m.start(), f.line))
    for m in CTX_R_RE.finditer(body):
        r = int(m.group(1))
        f.regs.setdefault(r, line_of(body, m.start(), f.line))
    if f.name in helpers:
        return  # a primitive: its offsets are taken at the call sites
    spvars = set(SP_ASSIGN_RE.findall(body))
    names = param_names(f.params)
    spvars |= {n for n in names if n in ("sp", "stackPointer")}
    # helper calls with an offset argument
    for hname, descs in helpers.items():
        for m in re.finditer(r"\b" + hname + r"\s*\(", body):
            args = call_args(body, m.end() - 1)
            for (oi, si, hf) in descs:
                if len(args) != len(hf.params):
                    continue
                if si is not None and args[si] not in spvars:
                    continue
                off = num(args[oi]) if oi < len(args) else None
                f.stack.append((off, line_of(body, m.start(), f.line), hname))
    # direct reads: <sp var | getRegU32(ctx,29)> [+ const] inside a memory read
    sp_alts = [SP_EXPR] + [r"\b" + re.escape(v) + r"\b" for v in spvars]
    for alt in sp_alts:
        for m in re.finditer(MEM_READ_FUNCS + r"\s*\(([^;]*?)(" + alt + r")\s*(?:\+\s*(0[xX][0-9a-fA-F]+|\d+)[uU]?)?\s*[,)]", body):
            # skip when the sp operand is part of a larger address (e.g. sp + offset param)
            off = int(m.group(3), 0) if m.group(3) else 0
            tail = body[m.end(2):m.end(2) + 40]
            if not m.group(3) and re.match(r"\s*\+\s*[A-Za-z_]", tail):
                continue
            f.stack.append((off, line_of(body, m.start(), f.line), "direct"))
    if vararg and re.search(r"\b(?:Ps2VarArgCursor|formatPs2StringWithArgs)\b", body):
        base, stride = vararg
        f.notes.append("varargs via Ps2VarArgCursor: slots 8+ read at sp+0x%x in %d-byte steps" % (base, stride))
        f.stack.append((base, f.line, "vararg-cursor"))
        if stride != 8:
            f.stack.append((base + stride, f.line, "vararg-cursor"))
    for m in re.finditer(r"(?:\b(ps2_syscalls)::)?\b(\w+)\s*\(", body):
        callee = m.group(2)
        before = body[max(0, m.start() - 2):m.start()]
        if not m.group(1) and (before.endswith(".") or before.endswith("->")):
            continue  # a member call (runtime->vfs().stat(...)), not the handler of that name
        if callee in funcnames and callee != f.name:
            seg = body[m.end() - 1:m.end() + 120]
            if "ctx" in seg.split(";")[0]:
                f.calls.add(("syscalls" if m.group(1) else None, callee))


def find_vararg_cursor(text):
    b = re.search(r"m_stackBase\s*\(\s*getRegU32\s*\(\s*ctx\s*,\s*29\s*\)\s*(?:\+\s*(0[xX][0-9a-fA-F]+|\d+))?", text)
    s = re.search(r"stackIndex\s*\*\s*(\d+)", text) or \
        re.search(r"\(\s*slotIndex\s*-\s*8u?\s*\)\s*\*\s*(\d+)", text)
    if not b:
        return None
    return (int(b.group(1), 0) if b.group(1) else 0, int(s.group(1)) if s else 4)


def comment_signature(f):
    """A prototype NAME(a, b, ...) in a comment inside or just above the handler."""
    text = f.raw_before[-600:] + f.raw_body
    for cm in re.finditer(r"//[^\n]*|/\*.*?\*/", text, re.S):
        m = re.search(r"\b" + re.escape(f.name) + r"\s*\(([^()]*)\)", cm.group(0))
        if m:
            inner = m.group(1).strip()
            if inner and (inner in ("stub",) or any(re.match(r"^[&\d]", a.strip()) for a in split_args(inner))):
                continue
            n = 0 if inner in ("", "void") else len(split_args(inner))
            return n, inner
    return None


def load_symdb():
    try:
        src = open(SYMDB, encoding="utf-8").read()
    except OSError:
        return {}
    blob = "".join(re.findall(r'R"PS2SDB\((.*?)\)PS2SDB"', src, re.S))
    try:
        data = json.loads(blob)
    except ValueError:
        return {}
    libs = {}
    for lib, syms in data.items():
        if isinstance(syms, dict):
            for s in syms:
                libs.setdefault(s, set()).add(lib)
    return libs


def load_call_lists():
    try:
        src = open(CALL_LIST, encoding="utf-8").read()
    except OSError:
        return set(), set()

    def grab(macro):
        m = re.search(r"#define\s+" + macro + r"\(X\)(.*?)(?:\n\s*\n|\n#)", src, re.S)
        return set(re.findall(r"X\((\w+)\)", m.group(1))) if m else set()
    return grab("PS2_SYSCALL_LIST"), grab("PS2_STUB_LIST")


def relpath(p):
    p = os.path.relpath(p, ROOT) if os.path.isabs(p) else p
    p = p.replace(os.sep, "/")
    k = p.find("ps2xRuntime/src/lib/Kernel/")
    return p[k + len("ps2xRuntime/src/lib/Kernel/"):] if k >= 0 else p


def resolve(name, names):
    if name in names:
        return name
    if name.startswith("_"):
        stripped = name.lstrip("_")
        if stripped in names:
            return stripped
    return None


def load_bound(path):
    bound = {}
    try:
        txt = open(path, encoding="utf-8").read()
    except OSError:
        return bound
    m = re.search(r"(?m)^stubs\s*=\s*\[(.*?)^\]", txt, re.S)
    if m:
        for name, addr in re.findall(r'"([^"@]+)@(0x[0-9a-fA-F]+)"', m.group(1)):
            bound.setdefault(name, []).append(addr)
    return bound



# ---- typed stub ABI layer (ps2xRuntime/include/ps2_stub_abi.h) ------------------------------
MACRO_RE = re.compile(r"\bPS2_STUB(0)?(_TYPED)?\s*\(")
ALIAS_RE = re.compile(r"\bPS2_STUB_ALIAS\s*\(\s*(\w+)\s*,\s*([\w:]+)\s*,\s*([\w:]+)\s*\)")
DISPATCH_RE = re.compile(r"\bPS2_STUB_DISPATCH\s*\(\s*(\w+)\s*\)")
DEVIATION_RE = re.compile(r'\bPS2_STUB_DEVIATION\s*\(\s*(\w+)\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)')
ARG_GPRS = list(range(4, 12))
TYPED_SUFFIXES = ("_legacy", "_typed", "_impl")


def param_class(param):
    """'int' | 'float' | 'varargs' for one declared parameter ('ps2_abi::gptr cd', 'float x')."""
    t = re.sub(r"\s*\b\w+\s*$", "", param.strip()) or param.strip()
    if re.search(r"\bVarArgs\b", t):
        return "varargs"
    if re.match(r"^(?:const\s+)?float$", t.strip()):
        return "float"
    return "int"


def declared_layout(params):
    """The EE layout of a declared parameter list, as ps2_abi::argLayout computes it."""
    ints = floats = stack = 0
    regs, fregs, slots, varargs_from = [], [], [], None
    for p in params:
        c = param_class(p)
        if c == "varargs":
            varargs_from = ints
        elif c == "float":
            if floats < 8:
                fregs.append(12 + floats)
            else:
                slots.append(stack)
                stack += 1
            floats += 1
        else:
            if ints < 8:
                regs.append(4 + ints)
            else:
                slots.append(stack)
                stack += 1
            ints += 1
    fp_stack = max(0, floats - 8)
    return {"int_args": ints, "float_args": floats, "stack_slots": stack, "varargs": varargs_from is not None,
            "varargs_from": varargs_from, "regs": regs, "fregs": fregs, "stack": [8 * s for s in slots],
            "fp_stack": fp_stack}


def parse_typed(path, src, kind):
    """Typed declarations in one source: (handlers, aliases, dispatched names).

    handlers: list of dicts {name, ret, params, form, line, body (clean), raw_body, start};
    the impl body is the brace block after the macro."""
    clean = strip_comments(src)
    handlers, aliases, dispatched = [], [], set()
    for m in MACRO_RE.finditer(clean):
        line_start = clean.rfind("\n", 0, m.start()) + 1
        if clean[line_start:m.start()].strip().startswith("#"):
            continue  # the macro's own #define
        args = call_args(clean, m.end() - 1)
        if len(args) < 2:
            continue
        # the closing parenthesis, then the impl body
        depth, close = 0, None
        for i in range(m.end() - 1, len(clean)):
            if clean[i] == "(":
                depth += 1
            elif clean[i] == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close is None:
            continue
        brace = clean.find("{", close)
        if brace < 0 or clean[close + 1:brace].strip():
            continue
        end = match_brace(clean, brace)
        handlers.append({
            "name": args[1], "ret": " ".join(args[0].split()), "params": [" ".join(a.split()) for a in args[2:]],
            "form": "typed" if not m.group(2) else "typed+legacy", "path": path,
            "line": clean.count("\n", 0, m.start()) + 1, "body": clean[brace:end + 1],
            "raw_body": src[brace:end + 1], "start": brace, "kind": kind,
        })
    for m in ALIAS_RE.finditer(clean):
        line_start = clean.rfind("\n", 0, m.start()) + 1
        if clean[line_start:m.start()].strip().startswith("#"):
            continue
        aliases.append({"name": m.group(1), "target": m.group(2).split("::")[-1],
                        "target_kind": "syscall" if m.group(3).endswith("Syscall") else "stub",
                        "line": clean.count("\n", 0, m.start()) + 1, "path": path, "kind": kind})
    for m in DISPATCH_RE.finditer(clean):
        line_start = clean.rfind("\n", 0, m.start()) + 1
        if not clean[line_start:m.start()].strip().startswith("#"):
            dispatched.add(m.group(1))
    deviations = {}
    for m in DEVIATION_RE.finditer(src):
        line_start = src.rfind("\n", 0, m.start()) + 1
        if src[line_start:m.start()].strip().startswith(("#", "//")) or clean[m.start()] == " ":
            continue
        deviations[m.group(1)] = m.group(2).replace('\\"', '"')
    for d in handlers:
        if d["name"] in deviations:
            d["deviation"] = deviations[d["name"]]
    for a in aliases:
        if a["name"] in deviations:
            a["deviation"] = deviations[a["name"]]
    return handlers, aliases, dispatched


def declared_signature(f):
    """(integer-class count, prototype, float stack slots) of a typed handler, or None."""
    d = getattr(f, "declared", None)
    if not d:
        return None
    lay = d["layout"]
    return lay["int_args"], d["prototype"], lay["fp_stack"]


def add_typed(funcs, sources):
    """Synthesise one handler Func per typed declaration and per alias; turn NAME_legacy /
    NAME_typed into helpers (folded into NAME's row)."""
    typed, aliases = [], []
    for path, text, kind in sources:
        if kind not in ("stub", "syscall"):
            continue
        h, a, dispatched = parse_typed(path, text, kind)
        for d in h:
            if d["form"] == "typed+legacy" and d["name"] not in dispatched:
                d["form"] = "typed+legacy (no PS2_STUB_DISPATCH)"
        typed.extend(h)
        aliases.extend(a)
    typed_keys = {(d["kind"], d["name"]) for d in typed}
    for f in funcs:
        for suffix in TYPED_SUFFIXES:
            if f.name.endswith(suffix) and (f.kind, f.name[:-len(suffix)]) in typed_keys:
                f.typed_part = suffix
                f.kind = "helper"
    out = []
    for d in typed:
        f = Func(d["name"], ["uint8_t *rdram", "R5900Context *ctx", "PS2Runtime *runtime"], d["path"], d["line"],
                 d["body"], d["raw_body"], d["start"], d["kind"])
        f.header_start = d["start"]
        f.raw_before = ""
        lay = declared_layout(d["params"])
        f.declared = {"ret": d["ret"], "params": d["params"], "form": d["form"], "layout": lay,
                      "prototype": ", ".join(d["params"]) + ("" if not lay["varargs"] else ""),
                      "deviation": d.get("deviation")}
        out.append(f)
    for a in aliases:
        f = Func(a["name"], ["uint8_t *rdram", "R5900Context *ctx", "PS2Runtime *runtime"], a["path"], a["line"],
                 "{ %s(rdram, ctx, runtime); }" % a["target"], "", 0, a["kind"])
        f.header_start = 0
        f.raw_before = ""
        f.alias = (a["target"], a["target_kind"])
        f.alias_deviation = a.get("deviation")
        out.append(f)
    return out


def finish_typed(funcs, byname):
    """After scan_body: record the impl's own argument-register reads, add the declared reads,
    resolve aliases, attach the legacy form."""
    for f in funcs:
        if getattr(f, "alias", None):
            target, tkind = f.alias
            f.calls = {("syscalls" if tkind == "syscall" else None, target)}
    for f in funcs:
        d = getattr(f, "declared", None)
        if not d:
            continue
        lay = d["layout"]
        # what the impl body (and the helpers it calls) reads by itself, before the declared reads
        iregs, istack, _ = effective(f, {k: v for k, v in byname.items() if not k.startswith("TODO")})
        f.impl_regs = sorted(r for r in iregs if r in ARG_GPRS)
        f.impl_stack = sorted({o for o, _, _ in istack if o is not None})
        for r in lay["regs"]:
            f.regs.setdefault(r, f.line)
        for o in lay["stack"]:
            f.stack.append((o, f.line, "declared"))
        if lay["varargs"]:
            f.notes.append("varargs (typed): slot %d on, a0-t3 then sp+0 in 8-byte slots" % lay["varargs_from"])
        f.legacy = None
        for g in byname.get(f.name + "_legacy", []):
            if g.kind == "helper" and g.path == f.path:
                f.legacy = g
    # Aliases, also of aliases (a stub forwarding to a syscall alias): resolve until nothing changes.
    for _ in range(8):
        changed = False
        for f in funcs:
            if getattr(f, "alias", None) and not getattr(f, "declared", None):
                target, tkind = f.alias
                for g in byname.get(target, []):
                    if getattr(g, "declared", None) and g.kind == tkind:
                        f.declared = dict(g.declared, form="alias of %s (%s)" % (target, tkind))
                        if getattr(f, "alias_deviation", None):
                            f.declared["deviation"] = f.alias_deviation
                        f.legacy = getattr(g, "legacy", None)
                        changed = True
                        break
        if not changed:
            break


def typed_findings(f, byname):
    """LEGACYDIFF / IMPLREG / SIGDIFF for a typed handler, and the legacy form's reads."""
    out, legacy = [], None
    d = getattr(f, "declared", None)
    if not d:
        return out, legacy
    lay = d["layout"]
    if getattr(f, "impl_regs", None):
        out.append(("IMPLREG", "the impl body reads argument register(s) %s itself" %
                    ",".join(map(str, f.impl_regs))))
    builtin = SDK_SIGS.get(f.name)
    dev = d.get("deviation")
    if dev:
        out.append(("DEVIATION", "declared deviation from the SDK prototype: " + dev))
    if builtin and builtin[0] != lay["int_args"]:
        if dev:
            out.append(("DEVIATION", "declared %d integer args, the builtin table says %d (%s): covered by the "
                        "deviation above" % (lay["int_args"], builtin[0], builtin[1])))
        else:
            out.append(("SIGDIFF", "declared %d integer args, the builtin table says %d (%s)" %
                        (lay["int_args"], builtin[0], builtin[1])))
    lf = getattr(f, "legacy", None)
    if lf is not None:
        regs, stack, _ = effective(lf, byname)
        lregs = sorted(r for r in regs if r in ARG_GPRS)
        lstack = sorted({o for o, _, _ in stack if o is not None})
        lvar = any("Ps2VarArgCursor" in n for n in lf.notes)
        legacy = {"line": lf.line, "regs": lregs, "stack": lstack, "varargs_cursor": lvar}
        dregs = set(lay["regs"])
        if lay["varargs"]:
            dregs |= set(range(4 + lay["varargs_from"], 12))  # vararg slots in a0-t3
        dstack = set(lay["stack"])
        extra_r = [r for r in lregs if r not in dregs]
        extra_s = [o for o in lstack if o not in dstack and not (lvar and lay["varargs"])]
        if extra_r or extra_s:
            out.append(("LEGACYDIFF", "the legacy form reads %s, which the declaration does not decode" %
                        ", ".join(["reg %d" % r for r in extra_r] + ["sp+0x%x" % o for o in extra_s])))
    return out, legacy


def load_signatures(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return {(h["kind"], h["name"]): h for h in data.get("handlers", [])}


def dump_findings(rows, dump):
    """DUMPDIFF per row against the ps2x_stubsigs dump; returns (matched, missing in source)."""
    seen, matched = set(), 0
    for r in rows:
        t = r.get("typed")
        if not t:
            continue
        key = (r["kind"], r["name"])
        seen.add(key)
        h = dump.get(key)
        if h is None:
            r["findings"].append({"code": "DUMPDIFF", "msg": "declared in the source, missing from the build's dump"})
            r["flagged"] = True
            continue
        lay = t["layout"]
        diffs = [k for k in ("int_args", "float_args", "stack_slots", "varargs") if h.get(k) != lay[k]]
        if bool(h.get("deviation")) != bool(t.get("deviation")):
            r["findings"].append({"code": "DUMPDIFF", "msg": "deviation marked in the %s only" % (
                "build's dump" if h.get("deviation") else "source")})
            r["flagged"] = True
            continue
        if diffs:
            r["findings"].append({"code": "DUMPDIFF", "msg": "the build's dump differs in %s (dump %s, source %s)" % (
                ", ".join(diffs), {k: h.get(k) for k in diffs}, {k: lay[k] for k in diffs})})
            r["flagged"] = True
        else:
            matched += 1
    missing = sorted("%s %s" % k for k in dump if k not in seen)
    return matched, missing


def gather(sources):
    """sources: list of (path, text, kind) with kind 'stub' | 'syscall' | 'helper'."""
    funcs = []
    for path, text, kind in sources:
        def kind_of(name, params, kind=kind):
            sig = len(params) == 3 and "R5900Context" in params[1] and "rdram" in params[0]
            return kind if (sig and kind in ("stub", "syscall")) else "helper"
        funcs.extend(parse_functions(path, text, kind_of))
    funcs.extend(add_typed(funcs, sources))
    vararg = None
    for _, text, _ in sources:
        vararg = vararg or find_vararg_cursor(text)
    helpers = find_stack_helpers(funcs)
    byname = {}
    for f in funcs:
        byname.setdefault(f.name, []).append(f)
    for f in funcs:
        scan_body(f, helpers, byname, vararg)
    finish_typed(funcs, byname)
    return funcs, byname, helpers, vararg


def effective(f, byname, seen=None):
    """Own reads plus those of helpers it calls (transitively)."""
    seen = seen if seen is not None else set()
    regs, stack, via = dict(f.regs), list(f.stack), []
    for ns, callee in sorted(f.calls, key=lambda c: c[1]):
        for g in byname.get(callee, []):
            if ns == "syscalls" and g.kind == "stub":
                continue
            if ns is None and g.kind == "syscall" and f.kind == "stub":
                continue
            key = (g.path, g.line)
            if key in seen:
                continue
            seen.add(key)
            r2, s2, v2 = effective(g, byname, seen)
            if r2 or s2:
                via.append("%s (%s:%d)" % (g.name, relpath(g.path), g.line))
            for r, ln in r2.items():
                regs.setdefault(r, ln)
            stack.extend(s2)
            via.extend(v2)
    return regs, stack, via


def check(name, nargs, regs, stack, fp_stack=0):
    """Return a list of (code, message) findings. fp_stack: stack slots taken by float overflow."""
    out = []
    offs = sorted({o for o, _, _ in stack if o is not None})
    valid = None
    if nargs is not None:
        valid = {8 * i for i in range(max(0, nargs - 8) + fp_stack)}
    for o in offs:
        if o % 8 != 0:
            out.append(("SLOT4", "stack read at sp+0x%x is not on an 8-byte slot" % o))
    for o in offs:
        if nargs is None:
            if 16 <= o <= 28:
                out.append(("O32HOME?", "reads sp+0x%x (an O32 arg 5-8 home slot); signature unknown" % o))
            continue
        if o in valid:
            continue
        if 16 <= o <= 28 and nargs <= 10:
            what = "args 5-8 (t0-t3)" if nargs > 4 else "arguments that do not exist"
            out.append(("O32HOME", "reads sp+0x%x standing in for %s; %d args, stack args at %s" %
                        (o, what, nargs, sorted(valid) or "none")))
        else:
            out.append(("PASTARGS", "reads sp+0x%x but %d args put stack args only at %s" %
                        (o, nargs, sorted(valid) or "none")))
    if nargs is not None and nargs > 4:
        need = list(range(8, min(11, 4 + nargs - 1) + 1))
        got = [r for r in need if r in regs]
        if not got and offs:
            out.append(("NOTREG", "%d args but never reads t0-t3 (regs %s); looks on the stack instead" %
                        (nargs, ",".join(map(str, need)))))
        elif not got:
            out.append(("IGNORED", "%d args but never reads t0-t3 (regs %s): arguments 5+ unused" %
                        (nargs, ",".join(map(str, need)))))
        elif len(got) < len(need):
            out.append(("PARTIAL", "reads only regs %s of %s (an argument may be ignored)" %
                        (",".join(map(str, got)), ",".join(map(str, need)))))
    if any(r in regs for r in range(8, 12)) and any(16 <= o <= 28 and (valid is None or o not in valid) for o in offs):
        out.append(("FALLBACK", "reads both t-regs and sp+16..28 (register-or-stack heuristic)"))
    return out


SEVERE = {"SLOT4", "O32HOME", "O32HOME?", "PASTARGS", "NOTREG"}


def audit(sources, bound=None, symdb=None, call_lists=(set(), set())):
    funcs, byname, helpers, vararg = gather(sources)
    syscalls, stubs = call_lists
    rows = []
    for f in funcs:
        if f.kind not in ("stub", "syscall"):
            continue
        if f.kind == "syscall" and syscalls and f.name not in syscalls and not getattr(f, "declared", None):
            continue  # (a typed syscall handler is kept: it is in the build's signature dump either way)
        regs, stack, via = effective(f, byname)
        sig, src = None, None
        ds = declared_signature(f)
        cs = None if ds else comment_signature(f)
        if ds:
            sig, src = ds, "declared"
        elif cs:
            sig, src = cs, "comment"
        elif f.name in SDK_SIGS:
            sig, src = SDK_SIGS[f.name], "builtin"
        nargs = sig[0] if sig else None
        findings = check(f.name, nargs, regs, stack, sig[2] if sig and len(sig) > 2 else 0)
        if getattr(f, "declared", None) and f.declared["layout"]["varargs"]:
            # a varargs handler reads past its fixed arguments by design
            findings = [x for x in findings if x[0] not in ("PASTARGS",)]
        tf, legacy = typed_findings(f, byname)
        findings += tf
        shadowed = f.kind == "stub" and syscalls and resolve(f.name, syscalls) is not None
        row = {
            "name": f.name, "kind": f.kind, "file": relpath(f.path),
            "line": f.line, "nargs": nargs, "sig_source": src, "prototype": sig[1] if sig else None,
            "treg_reads": sorted(r for r in regs if 8 <= r <= 11),
            "max_reg": max([r for r in regs if 4 <= r <= 11], default=None),
            "stack_reads": [{"offset": o, "line": ln, "via": how} for o, ln, how in stack],
            "helpers": via, "notes": f.notes, "findings": [{"code": c, "msg": m} for c, m in findings],
            "flagged": any(c in SEVERE for c, _ in findings),
            "shadowed_by_syscall": bool(shadowed),
            "library": sorted(symdb.get(f.name, ())) if symdb else [],
        }
        if getattr(f, "declared", None):
            row["typed"] = {"form": f.declared["form"], "ret": f.declared["ret"], "params": f.declared["params"],
                            "layout": {k: v for k, v in f.declared["layout"].items()},
                            "deviation": f.declared.get("deviation")}
            row["legacy_reads"] = legacy
        if bound is not None:
            hit = [b for b in bound if resolve(b, {f.name})]
            live = (f.kind == "syscall") or not shadowed
            row["bound"] = [a for b in hit for a in bound[b]] if live else []
            row["bound_but_shadowed"] = bool(hit) and not live
        rows.append(row)
    return rows, helpers, vararg


def default_sources():
    srcs = []
    stubdir = os.path.join(KERNEL, "Stubs")
    for fn in sorted(os.listdir(stubdir)):
        p = os.path.join(stubdir, fn)
        if fn.endswith(".cpp"):
            srcs.append((p, open(p, encoding="utf-8", errors="replace").read(), "stub"))
    for sub in ("Stubs/Helpers", "Syscalls", "Syscalls/Helpers"):
        d = os.path.join(KERNEL, sub)
        for fn in sorted(os.listdir(d)):
            p = os.path.join(d, fn)
            if fn.endswith((".cpp", ".h")) and os.path.isfile(p):
                kind = "syscall" if (sub == "Syscalls" and fn.endswith(".cpp")) else "helper"
                srcs.append((p, open(p, encoding="utf-8", errors="replace").read(), kind))
    return srcs


def fmt_row(r):
    st = ", ".join("sp+0x%x@%d(%s)" % (s["offset"], s["line"], s["via"]) if s["offset"] is not None
                   else "sp+?@%d(%s)" % (s["line"], s["via"]) for s in r["stack_reads"]) or "-"
    head = "%s  [%s] %s:%d  args=%s (%s)" % (r["name"], r["kind"], r["file"], r["line"],
                                             "?" if r["nargs"] is None else r["nargs"], r["sig_source"] or "no signature")
    if "bound" in r:
        head += "  BOUND " + ",".join(r["bound"]) if r["bound"] else ("  bound-but-shadowed-by-syscall" if r.get("bound_but_shadowed") else "")
    elif r["shadowed_by_syscall"]:
        head += "  (shadowed by syscall)"
    lines = [head, "    t-reg reads: %s   stack reads: %s" % (",".join(map(str, r["treg_reads"])) or "-", st)]
    if r.get("typed"):
        t = r["typed"]
        lines.append("    typed (%s): %s %s(%s)" % (t["form"], t["ret"], r["name"], ", ".join(t["params"])))
        if t.get("deviation"):
            lines.append("    deviation (declared): " + t["deviation"])
        lg = r.get("legacy_reads")
        if lg:
            lines.append("    legacy form (line %d) reads: %s" % (lg["line"], ", ".join(
                ["reg %d" % x for x in lg["regs"]] + ["sp+0x%x" % o for o in lg["stack"]] +
                (["varargs cursor"] if lg["varargs_cursor"] else [])) or "-"))
    if r["helpers"]:
        lines.append("    via: " + "; ".join(r["helpers"]))
    for n in r["notes"]:
        lines.append("    note: " + n)
    for fd in r["findings"]:
        lines.append("    %-9s %s" % (fd["code"], fd["msg"]))
    return "\n".join(lines)


SELFTEST_SRC = r'''
namespace ps2_stubs {
    static uint32_t readStackU32(uint8_t *rdram, R5900Context *ctx, uint32_t offset)
    {
        uint32_t sp = getRegU32(ctx, 29);
        const uint8_t *ptr = getConstMemPtr(rdram, sp + offset);
        return ptr ? *ptr : 0;
    }
    static uint32_t argFive(uint8_t *rdram, R5900Context *ctx) { return readStackU32(rdram, ctx, 16); }
    // sceBadSix(a, b, c, d, e, f): reads e/f from the O32 home slots
    void sceBadSix(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t d = getRegU32(ctx, 7); // "$a3 ) {"
        const uint32_t e = readStackU32(rdram, ctx, 16);
        const uint32_t f = readStackU32(rdram, ctx, 20);
    }
    // sceGoodTen(a, b, c, d, e, f, g, h, i, j)
    void sceGoodTen(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t e = getRegU32(ctx, 8), f = getRegU32(ctx, 9), g = GPR_U64(ctx, 10), h = getRegU32(ctx, 11);
        uint32_t i = readStackU32(rdram, ctx, 0), j = readStackU32(rdram, ctx, 8);
    }
    // sceNoTregs(a, b, c, d, e)
    void sceNoTregs(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        if (getRegU32(ctx, 4)) { return; }
    }
    // sceViaHelper(a, b, c, d, e)
    void sceViaHelper(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)argFive(rdram, ctx);
    }
    // sceDirect(a, b, c, d, e, f, g, h, i, j, k)
    void sceDirect(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t sp = getRegU32(ctx, 29);
        uint32_t x = FAST_READ32(sp + 0x14u);
        uint32_t y = FAST_READ32(sp);
        uint32_t t = getRegU32(ctx, 8);
    }
    void sceUnknown(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t z = FAST_READ32(getRegU32(ctx, 29) + 0x18);
    }
    void sceDelegates(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ps2_syscalls::SysThing(rdram, ctx, runtime);
    }
}
'''

SELFTEST_SYS = r'''
static bool readStackU32(uint8_t *rdram, uint32_t sp, uint32_t offset, uint32_t &out)
{
    uint8_t *ptr = getMemPtr(rdram, sp + offset);
    return ptr != nullptr;
}
namespace ps2_syscalls {
    // SysThing(a, b, c, d, e, f)
    void SysThing(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        uint32_t sp = getRegU32(ctx, 29);
        uint32_t v = 0;
        readStackU32(rdram, sp, 0x10, v);
    }
}
'''


def selftest():
    rows, helpers, _ = audit([("t/Stubs.cpp", SELFTEST_SRC, "stub"), ("t/Sys.cpp", SELFTEST_SYS, "syscall")],
                             bound={"sceBadSix": ["0x100"]}, call_lists=({"SysThing"}, set()))
    by = {r["name"]: r for r in rows}
    codes = {n: sorted({f["code"] for f in r["findings"]}) for n, r in by.items()}
    expect = {
        "sceBadSix": ["NOTREG", "O32HOME", "SLOT4"],
        "sceGoodTen": [],
        "sceNoTregs": ["IGNORED"],
        "sceViaHelper": ["NOTREG", "O32HOME"],
        "sceDirect": ["FALLBACK", "PARTIAL", "PASTARGS", "SLOT4"],
        "sceUnknown": ["O32HOME?"],
        "sceDelegates": ["O32HOME?"],
        "SysThing": ["NOTREG", "O32HOME"],
    }
    ok = True
    for n, want in expect.items():
        got = codes.get(n)
        status = "ok" if got == want else "FAIL"
        ok &= got == want
        print("%-13s %-4s %s" % (n, status, ",".join(got or []) or "(clean)"))
    checks = [
        ("readStackU32 found as a helper", "readStackU32" in helpers),
        ("sceBadSix offsets 16,20", sorted(s["offset"] for s in by["sceBadSix"]["stack_reads"]) == [16, 20]),
        ("sceDirect offsets 0,20", sorted(s["offset"] for s in by["sceDirect"]["stack_reads"]) == [0, 20]),
        ("sceGoodTen sig from comment", by["sceGoodTen"]["sig_source"] == "comment" and by["sceGoodTen"]["nargs"] == 10),
        ("sceBadSix bound", by["sceBadSix"].get("bound") == ["0x100"]),
        ("sceDelegates follows syscall", any("SysThing" in h for h in by["sceDelegates"]["helpers"])),
    ]
    for label, cond in checks:
        ok &= bool(cond)
        print("%-34s %s" % (label, "ok" if cond else "FAIL"))
    ok &= selftest_typed()
    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


SELFTEST_TYPED = r'''
namespace ps2_stubs {
    static uint32_t readStackU32(uint8_t *rdram, R5900Context *ctx, uint32_t offset)
    {
        uint32_t sp = getRegU32(ctx, 29);
        const uint8_t *ptr = getConstMemPtr(rdram, sp + offset);
        return ptr ? *ptr : 0;
    }
    // sceTypedSix(a, b, c, d, e, f): the legacy form read e/f from the O32 home slots
    PS2_STUB_TYPED(int32_t, sceTypedSix, uint32_t a, ps2_abi::gptr b, uint32_t c, uint32_t d, uint32_t e,
                   uint32_t f)
    {
        return static_cast<int32_t>(a + b + c + d + e + f);
    }
    void sceTypedSix_legacy(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t a = getRegU32(ctx, 4);
        const uint32_t e = readStackU32(rdram, ctx, 16);
        setReturnS32(ctx, a + e);
    }
    PS2_STUB_DISPATCH(sceTypedSix)

    PS2_STUB(int64_t, sceTypedTen, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e, uint32_t f,
             uint32_t g, uint32_t h, uint32_t i, uint64_t j)
    {
        return i + j;
    }

    // the impl reads t0 itself instead of taking it as a parameter
    PS2_STUB(uint32_t, sceTypedImplReg, uint32_t a)
    {
        return a + getRegU32(ctx, 8);
    }

    PS2_STUB(float, sceTypedFloats, ps2_abi::gptr m, float f1, float f2, float f3, float f4, float f5, float f6,
             float f7, float f8, float f9)
    {
        return f9;
    }

    PS2_STUB(int32_t, sceTypedPrintf, ps2_abi::gptr fmt, ps2_abi::VarArgs args)
    {
        return 0;
    }

    PS2_STUB0(void, sceTypedNoArgs)
    {
    }

    PS2_STUB_TYPED(int32_t, sceTypedNoDispatch, uint32_t a)
    {
        return a;
    }
    void sceTypedNoDispatch_legacy(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        setReturnS32(ctx, getRegU32(ctx, 4));
    }

    PS2_STUB_ALIAS(sceTypedAlias, ps2_syscalls::SysTyped, ::ps2_abi::Kind::Syscall)

    PS2_STUB_TYPED(int32_t, sceTypedDev, uint32_t a)
    {
        return a;
    }
    void sceTypedDev_legacy(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        setReturnS32(ctx, getRegU32(ctx, 4));
    }
    PS2_STUB_DISPATCH(sceTypedDev)
    PS2_STUB_DEVIATION(sceTypedDev, "SDK: void sceTypedDev(int a); writes v0 (kept)")

    PS2_STUB_ALIAS(sceTypedAliasChain, ps2_syscalls::SysTypedAlias, ::ps2_abi::Kind::Syscall)
}
'''

SELFTEST_TYPED_SYS = r'''
namespace ps2_syscalls {
    PS2_STUB(int32_t, SysTyped, uint32_t a, uint32_t b, uint32_t c, uint32_t d, uint32_t e)
    {
        return a + e;
    }
    PS2_STUB_ALIAS(SysTypedAlias, SysTyped, ::ps2_abi::Kind::Syscall)
}
'''


def selftest_typed():
    rows, _, _ = audit([("t/Typed.cpp", SELFTEST_TYPED, "stub"), ("t/SysTyped.cpp", SELFTEST_TYPED_SYS, "syscall")],
                       call_lists=({"SysTyped", "SysTypedAlias"}, set()))
    by = {(r["kind"], r["name"]): r for r in rows}
    stubs = {r["name"]: r for r in rows if r["kind"] == "stub"}
    dump = {("stub", n): {"int_args": r["typed"]["layout"]["int_args"], "float_args": r["typed"]["layout"]["float_args"],
                          "stack_slots": r["typed"]["layout"]["stack_slots"], "varargs": r["typed"]["layout"]["varargs"]}
            for n, r in stubs.items() if r.get("typed")}
    dump[("syscall", "SysTyped")] = {"int_args": 5, "float_args": 0, "stack_slots": 0, "varargs": False}
    dump[("stub", "sceTypedTen")] = dict(dump[("stub", "sceTypedTen")], stack_slots=1)  # a wrong dump entry
    dump[("stub", "sceOnlyInDump")] = {"int_args": 1, "float_args": 0, "stack_slots": 0, "varargs": False}
    dump[("syscall", "SysTypedAlias")] = {"int_args": 5, "float_args": 0, "stack_slots": 0, "varargs": False}
    dump[("stub", "sceTypedDev")] = dict(dump[("stub", "sceTypedDev")], deviation="SDK: void")
    dump[("stub", "sceTypedNoArgs")] = dict(dump[("stub", "sceTypedNoArgs")], deviation="only in the dump")
    matched, missing = dump_findings(rows, dump)
    codes = {n: sorted({f["code"] for f in r["findings"]}) for n, r in stubs.items()}
    t6 = stubs.get("sceTypedSix", {})
    checks = [
        ("typed rows replace _legacy/_impl", "sceTypedSix_legacy" not in stubs and "sceTypedSix" in stubs),
        ("sceTypedSix declared, 6 args, clean but LEGACYDIFF",
         t6.get("sig_source") == "declared" and t6.get("nargs") == 6 and codes.get("sceTypedSix") == ["LEGACYDIFF"]),
        ("sceTypedSix legacy reads a0 + sp+0x10", (t6.get("legacy_reads") or {}).get("stack") == [16] and
         (t6.get("legacy_reads") or {}).get("regs") == [4]),
        ("sceTypedSix reads t0,t1 (declared)", t6.get("treg_reads") == [8, 9]),
        ("sceTypedTen: stack slots 0,8, clean", sorted(s["offset"] for s in stubs["sceTypedTen"]["stack_reads"]) == [0, 8]
         and codes.get("sceTypedTen") == ["DUMPDIFF"]),
        ("sceTypedImplReg: IMPLREG", codes.get("sceTypedImplReg") == ["IMPLREG"]),
        ("sceTypedFloats: 1 int, 9 floats, ninth at sp+0",
         stubs["sceTypedFloats"]["typed"]["layout"]["float_args"] == 9 and
         stubs["sceTypedFloats"]["typed"]["layout"]["stack"] == [0] and codes.get("sceTypedFloats") == []),
        ("sceTypedPrintf: varargs from slot 1", stubs["sceTypedPrintf"]["typed"]["layout"]["varargs_from"] == 1),
        ("sceTypedNoArgs: PS2_STUB0", stubs.get("sceTypedNoArgs", {}).get("nargs") == 0),
        ("sceTypedNoDispatch: form says no dispatch", "no PS2_STUB_DISPATCH" in stubs["sceTypedNoDispatch"]["typed"]["form"]),
        ("alias resolves to the syscall's signature", stubs.get("sceTypedAlias", {}).get("nargs") == 5 and
         "alias" in stubs["sceTypedAlias"]["typed"]["form"]),
        ("syscall typed row", by.get(("syscall", "SysTyped"), {}).get("sig_source") == "declared"),
        ("dump: DUMPDIFF on a wrong entry, a missing one and a deviation only in the dump",
         missing == ["stub sceOnlyInDump"] and matched == len(dump) - 3 and
         "DUMPDIFF" in codes.get("sceTypedNoArgs", [])),
        ("sceTypedDev: DEVIATION (info), deviation text kept",
         codes.get("sceTypedDev") == ["DEVIATION"] and
         stubs["sceTypedDev"]["typed"]["deviation"] == "SDK: void sceTypedDev(int a); writes v0 (kept)"),
        ("alias of an alias resolves", stubs.get("sceTypedAliasChain", {}).get("nargs") == 5),
    ]
    ok = True
    for label, cond in checks:
        ok &= bool(cond)
        print("%-50s %s" % (label, "ok" if cond else "FAIL"))
    if not ok:
        for n in sorted(codes):
            print("   ", n, codes[n])
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--bound", help="srwz.toml: mark handlers SRWZ binds")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--all", action="store_true", help="print every handler, not only flagged ones")
    ap.add_argument("--root", help="the PS2Recomp tree to scan (default: third_party/PS2Recomp)")
    ap.add_argument("--signatures", help="ps2x_stubsigs JSON dump: cross-check the typed declarations")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if a.root:
        set_root(os.path.abspath(a.root))
    bound = load_bound(a.bound) if a.bound else None
    rows, helpers, vararg = audit(default_sources(), bound=bound, symdb=load_symdb(), call_lists=load_call_lists())
    dump_line = None
    if a.signatures:
        dump = load_signatures(a.signatures)
        matched, missing = dump_findings(rows, dump)
        for name in missing:
            print("DUMPDIFF  in the build's dump but not declared in the source: " + name)
        dump_line = "dump: %d handlers, %d matched%s" % (len(dump), matched,
                                                         (", %d missing in the source" % len(missing)) if missing else "")
    if a.json:
        json.dump({"helpers": sorted(helpers), "vararg_cursor": vararg, "handlers": rows, "dump": dump_line},
                  sys.stdout, indent=1)
        print()
        return 0
    flagged = [r for r in rows if r["flagged"]]
    print("stack-read helpers: %s" % ", ".join("%s (%s)" % (h, "; ".join("%s:%d" % (relpath(d[2].path), d[2].line) for d in v))
                                              for h, v in sorted(helpers.items())))
    if vararg:
        print("vararg cursor: slots 8+ at sp+0x%x, %d-byte stride (EE ABI: sp+0x0, 8-byte)" % vararg)
    print("handlers scanned: %d (%d stubs, %d syscalls); with a signature: %d; flagged: %d"
          % (len(rows), sum(r["kind"] == "stub" for r in rows), sum(r["kind"] == "syscall" for r in rows),
             sum(r["nargs"] is not None for r in rows), len(flagged)))
    typed = [r for r in rows if r.get("typed")]
    print("typed (ps2_stub_abi.h): %d (%d with a legacy form, %d aliases); LEGACYDIFF %d, IMPLREG %d, SIGDIFF %d,"
          " declared deviations %d"
          % (len(typed), sum(1 for r in typed if r.get("legacy_reads")),
             sum(1 for r in typed if r["typed"]["form"].startswith("alias")),
             sum(1 for r in typed for x in r["findings"] if x["code"] == "LEGACYDIFF"),
             sum(1 for r in typed for x in r["findings"] if x["code"] == "IMPLREG"),
             sum(1 for r in typed for x in r["findings"] if x["code"] == "SIGDIFF"),
             sum(1 for r in typed if r["typed"].get("deviation"))))
    if dump_line:
        print(dump_line)
    if bound is not None:
        print("flagged and bound by SRWZ: %d" % sum(1 for r in flagged if r.get("bound")))
        nosig = [r for r in rows if r.get("bound") and r["nargs"] is None]
        print("bound handlers without a signature (unchecked for t0-t3): %d" % len(nosig))
        hi = [r["name"] for r in nosig if r["max_reg"] is not None and r["max_reg"] >= 7 and not r["treg_reads"]]
        if hi:
            print("  of those, read a3 but no t-reg (check the arg count by hand): " + ", ".join(sorted(hi)))
    print("paths are relative to %s" % os.path.relpath(KERNEL, ROOT).replace(os.sep, "/"))
    print()
    for r in (rows if a.all else flagged):
        print(fmt_row(r))
    others = [r for r in rows if not r["flagged"] and r["findings"]]
    if others and not a.all:
        print("\ninfo only (IGNORED/PARTIAL/FALLBACK: arguments unused or register-first, not misread;"
              " LEGACYDIFF/IMPLREG/SIGDIFF/DEVIATION: typed handlers):")
        for r in others:
            print(fmt_row(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
