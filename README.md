# 4d-plugin-trash
Move a file system item to trash.

### Platform

| carbon | cocoa | win32 | win64 |
|:------:|:-----:|:---------:|:---------:|
||<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|

### Version

<img width="32" height="32" src="https://user-images.githubusercontent.com/1725068/73986501-15964580-4981-11ea-9ac1-73c5cee50aae.png"> <img src="https://user-images.githubusercontent.com/1725068/73987971-db2ea780-4984-11ea-8ada-e25fb9c3cf4e.png" width="32" height="32" />

### Remarks

On Mac, [NSFileManager](https://developer.apple.com/documentation/foundation/nsfilemanager?language=objc) is used for synchronous operation, [NSWorkspace](https://developer.apple.com/documentation/appkit/nsworkspace?language=objc) is used for asynchronous operation. A file set to "locked" in Finder (Get Info) can not be trashed.

On Windows, [SHFileOperation](https://docs.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shfileoperationa) is used. A new thread is started for asynchronous operation.

### Examples

```
$path:=System folder(Desktop)+"test_synchronous.txt"
CLOSE DOCUMENT(Create document($path))

$status:=Trash item ($path)  //default:Trash synchronous

$path:=System folder(Desktop)+"test_asynchronous.txt"
CLOSE DOCUMENT(Create document($path))

$status:=Trash item ($path;Trash asynchronous)
```
