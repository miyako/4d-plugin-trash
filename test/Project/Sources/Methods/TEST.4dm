//%attributes = {}
$path:=System folder:C487(Desktop:K41:16)+"test_synchronous.txt"
CLOSE DOCUMENT:C267(Create document:C266($path))

$status:=Trash item ($path)  //default:Trash synchronous

$path:=System folder:C487(Desktop:K41:16)+"test_asynchronous.txt"
CLOSE DOCUMENT:C267(Create document:C266($path))

$status:=Trash item ($path;Trash asynchronous)

