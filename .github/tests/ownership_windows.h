// Syntax-only Windows consumer gate on macOS, not a Windows SDK or ABI substitute.
#include "4DPlugin-Trash.h"
#include <cstring>
#include <cwchar>
#include <cstdlib>
#undef VERSIONMAC
#undef VERSIONWIN
#define VERSIONMAC 0
#define VERSIONWIN 1
#define __stdcall

using DWORD = unsigned int;
using ULONGLONG = unsigned long long;
using SIZE_T = size_t;
using HANDLE = void*;
using LPVOID = void*;
using PCZZWSTR = const wchar_t*;
using RPC_WSTR = unsigned short*;
using RPC_STATUS = int;
struct UUID { unsigned char bytes[16]; };
struct MEMORY_BASIC_INFORMATION { SIZE_T RegionSize; };
struct SHFILEOPSTRUCTW {
    void* hwnd;
    unsigned int wFunc;
    unsigned short fFlags;
    PCZZWSTR pFrom;
    const wchar_t* pTo;
    BOOL fAnyOperationsAborted;
};
enum {
    RPC_S_OK = 0, RPC_S_UUID_LOCAL_ONLY = 1824,
    FILE_MAP_READ = 4, FILE_MAP_WRITE = 2, EVENT_MODIFY_STATE = 2,
    PAGE_READWRITE = 4, ERROR_ALREADY_EXISTS = 183, FO_DELETE = 3,
    FOF_ALLOWUNDO = 64, FOF_NO_UI = 1556,
    WAIT_OBJECT_0 = 0, WAIT_TIMEOUT = 258
};
#define INVALID_HANDLE_VALUE reinterpret_cast<HANDLE>(-1)

[[noreturn]] inline void OwnershipUnexpectedWindows() { std::abort(); }
inline RPC_STATUS UuidCreate(UUID*) { OwnershipUnexpectedWindows(); }
inline RPC_STATUS UuidToString(UUID*, RPC_WSTR*) { OwnershipUnexpectedWindows(); }
inline RPC_STATUS RpcStringFree(RPC_WSTR*) { OwnershipUnexpectedWindows(); }
inline HANDLE OpenFileMapping(DWORD, BOOL, const wchar_t*) { OwnershipUnexpectedWindows(); }
inline LPVOID MapViewOfFile(HANDLE, DWORD, DWORD, DWORD, SIZE_T) { OwnershipUnexpectedWindows(); }
inline SIZE_T VirtualQuery(LPVOID, MEMORY_BASIC_INFORMATION*, SIZE_T) { OwnershipUnexpectedWindows(); }
inline void CopyMemory(void*, const void*, SIZE_T) { OwnershipUnexpectedWindows(); }
inline BOOL UnmapViewOfFile(LPVOID) { OwnershipUnexpectedWindows(); }
inline BOOL CloseHandle(HANDLE) { OwnershipUnexpectedWindows(); }
inline HANDLE OpenEvent(DWORD, BOOL, const wchar_t*) { OwnershipUnexpectedWindows(); }
inline BOOL SetEvent(HANDLE) { OwnershipUnexpectedWindows(); }
inline BOOL ResetEvent(HANDLE) { OwnershipUnexpectedWindows(); }
inline int SHFileOperationW(SHFILEOPSTRUCTW*) { OwnershipUnexpectedWindows(); }
inline HANDLE CreateFileMapping(HANDLE, void*, DWORD, DWORD, DWORD, const wchar_t*) { OwnershipUnexpectedWindows(); }
inline DWORD GetLastError() { OwnershipUnexpectedWindows(); }
inline HANDLE CreateEvent(void*, BOOL, BOOL, const wchar_t*) { OwnershipUnexpectedWindows(); }
inline uintptr_t _beginthreadex(void*, unsigned, unsigned (*)(void*), void*, unsigned, unsigned*) { OwnershipUnexpectedWindows(); }
inline DWORD WaitForMultipleObjects(DWORD, const HANDLE*, BOOL, DWORD) { OwnershipUnexpectedWindows(); }
inline DWORD WaitForSingleObject(HANDLE, DWORD) { OwnershipUnexpectedWindows(); }
