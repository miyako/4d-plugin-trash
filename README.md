# 4d-plugin-trash

Moves a file or folder to the system trash (macOS Trash, Windows Recycle Bin) instead of deleting it permanently. On macOS the plugin uses `NSFileManager` for the synchronous mode and `NSWorkspace` for the asynchronous mode. On Windows it uses the shell's `SHFileOperation` call, and starts a worker thread for the asynchronous mode. Every call returns an `Object` describing the outcome.

| Command | Returns | Purpose |
|---|---|---|
| [Trash item](#trash-item) | `Object` | Move a file or folder to the trash, synchronously or asynchronously |

**Platforms:** macOS (Cocoa) and Windows (32-bit and 64-bit). The command is declared thread-safe, so it can be called from preemptive processes.

> **Forward-looking notes.** Some behavior below exists only in the fixed source (`4DPlugin-Trash.cpp`, revised during code review) and is true once that source is built, not necessarily of a binary you already have installed. Each such point is marked **(fixed build)**.

---

## Requirements & platform notes

- **One command, two parameters.** `Trash item` takes the path as parameter 1 and an optional mode as parameter 2. The call `Trash item ($path)` with one argument appears in the plugin's own test method, which is why parameter 2 is documented as optional (inferred from that sample together with the plugin's default behavior; the manifest syntax alone doesn't say).
- **Modes.** `Trash synchronous` (the default when parameter 2 is omitted) and `Trash asynchronous` are constants provided by the plugin. Their numeric values are defined in the plugin's header, which I haven't seen, so always use the constants. Any value other than `Trash synchronous` behaves as asynchronous.
- **Asynchronous mode reports nothing about the final result.** It returns as soon as the request is handed over. Don't assume the item is already gone when the command returns, and don't touch the item again right away. See [Error handling & troubleshooting](#error-handling--troubleshooting).
- **Paths.** Pass a full system path to an existing item. On Windows, relative paths are not accepted **(fixed build)**; Microsoft's documentation also says `SHFileOperation` should be given fully qualified paths. On macOS, the examples build paths with `System folder`. I haven't confirmed which other path syntaxes the macOS side accepts, so test with the syntax your application uses.
- **Windows path length.** `SHFileOperation` doesn't support paths beyond `MAX_PATH` (260 characters); Microsoft lists error `0x79` (`DE_PATHTOODEEP`) for this case.
- **macOS locked files.** A file marked "Locked" in Finder (Get Info) can't be trashed.
- **Folders** are trashed together with their contents.
- **Trash may not always be possible.** I haven't verified this against the plugin, but the Windows call asks for the Recycle Bin without confirmation prompts. In general shell behavior, items that the Recycle Bin can't hold (for example some network or removable locations, or very large items) may be deleted permanently instead. Test your target locations if recoverability matters.

---

## Trash item

### Syntax

```4d
Trash item ( path {; mode} ) → Object
```

| Parameter | Type | Description |
|---|---|---|
| `path` | Text | Full system path of the file or folder to trash |
| `mode` | Integer | Optional. `Trash synchronous` (default) or `Trash asynchronous` |
| Result | Object | Outcome of the operation, described below |

### Description

**Synchronous mode** blocks until the operation finishes and tells you whether it worked.

- **On macOS**, the item is moved with `NSFileManager`. On success, `path` contains the item's new location inside the Trash (when the system reports one). On failure, `error` and `errorString` come from the system's `NSError`.
- **On Windows**, the item is moved with `SHFileOperation` with undo (Recycle Bin) and no UI. On success `success` is `True`. If the system reports an error, `success` is `False` and `error` holds its numeric code. If the operation reports that it was aborted without an error code, `success` is `False` and `error` is absent. No `path` is returned.

**Asynchronous mode** hands the request over and returns immediately.

- **On macOS**, the request is passed to `NSWorkspace` and the command returns an empty object. Nothing is reported afterwards.
- **On Windows**, the plugin starts a worker thread and waits only until that thread has picked up the request (yielding to other 4D processes while it waits), then returns an empty object. The actual trashing happens afterwards on the worker thread, and its result is not reported.
- In both cases an empty object (no `success` property) means "request handed over", **not** "item trashed".
- **(Fixed build)** If the request can't be handed over, the object contains `success: False` and an `error` code instead.

**Return object**

| Property | Type | Description |
|---|---|---|
| `success` | Boolean | `True` if the item was trashed (synchronous mode). `False` on failure. Absent when an asynchronous request was handed over successfully |
| `path` | Text | **macOS, synchronous only.** New location of the item in the Trash, when available |
| `error` | Number | Present on failure. See the error table below |
| `errorString` | Text | **macOS, synchronous only.** System error description |

**Error codes**

| Code | Source | Meaning |
|---|---|---|
| `90001` | Plugin **(fixed build)** | Invalid path: empty, not absolute, or (Windows) containing wildcards `*` `?` or a NUL character, or longer than 32767 characters. On macOS: the path couldn't be converted to a URL |
| `90002` | Plugin, Windows **(fixed build)** | Busy: another asynchronous call was being handed over at the same moment. Retry |
| `90003` | Plugin, Windows **(fixed build)** | The asynchronous request couldn't be handed over |
| `90004` | Plugin **(fixed build)** | Unexpected internal exception. The command still returns normally |
| other | macOS | The `NSError` code, for example `4` for a missing file (`NSFileNoSuchFileError`) |
| other | Windows | The return value of `SHFileOperation`. Microsoft notes that several values are pre-Win32 codes meant only as a debugging aid. For example `0x79` (121) means the path exceeded `MAX_PATH`, `0x7C` (124) an invalid path, `0x78` (120) access denied |

I haven't traced what the Windows call returns for a path that doesn't exist, so check `success` rather than relying on a specific code.

### Example

From the plugin's own test method (`TEST.4dm`). It creates a file on the Desktop, trashes it synchronously, then does the same asynchronously:

```4d
$path:=System folder:C487(Desktop:K41:16)+"test_synchronous.txt"
CLOSE DOCUMENT:C267(Create document:C266($path))

$status:=Trash item ($path)  //default:Trash synchronous

$path:=System folder:C487(Desktop:K41:16)+"test_asynchronous.txt"
CLOSE DOCUMENT:C267(Create document:C266($path))

$status:=Trash item ($path;Trash asynchronous)
```

The sample doesn't look at `$status`. Check it in real code:

```4d
$path:=System folder:C487(Desktop:K41:16)+"report.txt"
CLOSE DOCUMENT:C267(Create document:C266($path))

$status:=Trash item ($path)

If ($status.success)
	ALERT("Moved to the trash.")
Else 
	If (OB Is defined($status;"error"))
		ALERT("Could not trash the file. Error "+String($status.error))
	Else 
		ALERT("The operation was not completed.")
	End if 
End if 
```

Handling both modes in one method. In asynchronous mode, an object without `success` means the request was accepted:

```4d
$status:=Trash item ($path;Trash asynchronous)

Case of 
	: (Not(OB Is defined($status;"success")))
		//request handed over; outcome is not reported
		
	: ($status.success=False)
		//the request could not be handed over
		ALERT("Not queued. Error "+String($status.error))
End case 
```

---

## Error handling & troubleshooting

- **Check `success`, not just that a call returned.** The command doesn't raise a 4D error for a failed trash operation. In synchronous mode, read `success` and `error`.
- **Asynchronous mode is fire-and-forget.** An empty result object means the request was handed over, nothing more. There is no completion callback and no failure report, so use synchronous mode when you need to know the outcome.
- **Don't reuse a path right after an asynchronous call.** The item may still be in place for a short moment. Don't, for example, create a new file at the same path immediately.
- **Error `90002` (Windows, fixed build).** Two asynchronous calls arrived at the same moment. Wait briefly and retry; synchronous mode isn't affected.
- **Error `90001` (fixed build).** The path was rejected before any file operation. Use an absolute path without wildcards. This check exists so that a pattern such as `C:\folder\*.*` can't trash many items at once.
- **Locked file on macOS.** An item set to "Locked" in Finder can't be trashed. Unlock it first.
- **Long paths on Windows.** Paths over `MAX_PATH` fail (documented error `0x79`).
- **Windows `error` codes aren't standard Win32 codes.** Treat them as diagnostic hints, as Microsoft advises.
- **Unexpected asynchronous behavior on macOS.** I couldn't confirm from the source whether the asynchronous call is safe from every kind of 4D process, so test it from the process types you use (for example preemptive workers).

---

## Quick reference

```4d
//synchronous (default)
$status:=Trash item ($path)
If ($status.success)
	//trashed; on macOS $status.path holds the new location
End if 

//asynchronous: returns at once, outcome is not reported
$status:=Trash item ($path;Trash asynchronous)
If (OB Is defined($status;"success"))
	//$status.success=False: could not be queued, see $status.error
End if 
```
