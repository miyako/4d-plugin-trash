# Trash native ownership audit

## Provenance and inventory

Baseline: `cf110218c82d39540e598258b1fe53379300e60a`.
Canonical guidance: `miyako/skills`, immutable commit
`e6b492fcb81e5e1a7f7db723594b420e63bf6eb9`,
`4d-skills/skills/4dplugin/SKILL.md`, read through the GitHub API.
Issue: #2.

Both `manifest.json` and the committed bundle resource manifest declare the
single thread-safe command `Trash item(&T;&L):J`: scalar text, scalar longint,
object result, no array parameters. The actual `PluginMain` dispatches selector
1 to `Trash_item`; selectors 0/999 are no-ops. The inventory includes internal
and dormant code, not just manifest parameters.

| Tracked native surface | Build membership and ownership finding |
|---|---|
| `4DPlugin-Trash.cpp/.h` | Xcode and all MSBuild configurations. C_TEXT copies input before use. macOS `ob_set_s(NSString*)` reaches the UTF-16 JSON setter for path/error output. Returned object ownership passes to 4D. Existing NS object scope guards remain. Windows synchronous/worker paths use copied `std::wstring`/vectors; no PA string handoff. |
| `support/4DPlugin-JSON.cpp/.h` | Both projects compile all helpers. All four string setters overwrite an owning empty string; picture setter similarly overwrites an empty picture. Five property getters leak owning success/mismatch results. Matching object/collection getter results already hand off ownership, and must not be cleared. Stringify's independent duplicated-object argument must be cleared after its synchronous call, while successful return ownership is separately cleared. |
| `Classes/C_TEXT.cpp/.h` | Both projects. Independent `std::basic_string` storage; parameter and result setters call content setter, not descriptor-transfer setter. Already noncopyable. UTF-8/UTF-16 conversions use explicit byte/unit lengths; no converter createCopy method. Objective-C path helpers are included in the platform interception gate. |
| `Classes/ARRAY_TEXT.cpp/.h` | Tracked, dormant, absent from both project source lists. Reads borrowed array elements into independent C++ strings before mutation. Accepted insertions transfer to the array with no post-transfer disposal. Raw owned vector pointer exposed unsafe implicit copy/assignment; sanitizer-proven latent hazard. |
| `Classes/C_BLOB.cpp/.h` | Tracked, dormant, absent from both projects. CBytes uses value storage; C_BLOB owns a raw CBytes pointer and exposed unsafe implicit copy/assignment. Parameter replacement freed the old caller handle before attempting allocation, leaving a dangling parameter on failure. |
| `Classes/C_LONGINT.cpp/.h`, `C_TYPES.h` | Dormant integer wrapper has value-only state; shared aliases define UTF-16 units and byte strings. All wrappers compile in the synthetic host. |
| Complete `4DPluginAPI.c`, API/ABI headers, `.def` | Same SDK source compiled in both projects and the host, never rewritten or replaced with fragments. Header says v13, implementation v14; these comments are not independently verified SDK release provenance. Export `.def` includes engine entry point, not another application wrapper implementation. |
| `include/json/*.h`, `a/libjsoncpp.a`, Windows jsoncpp libraries | Tracked vendor JSON headers/opaque prebuilt libraries, no alternate PA wrapper. Current native sources do not include/use JsonCpp. No binary provenance or behavior certification; binaries untouched. |
| Committed bundle executables/resources, zip/dmg, `test/` 4D project | Prebuilt artifacts and real filesystem/trash test method are not run or modified. The 4D test creates desktop files and calls Trash, so is explicitly unsuitable for this audit. |

No CPicture/picture wrapper, setBytes picture method, createCopy converter,
pointer-reference wrapper constructor, additional native implementation, or
additional selector exists in this tracked source inventory. `ob_set_p` is the
only application picture helper; it is compiled but not called by Trash.
There are no application callback helpers; asynchronous Windows work does not
retain a PA string descriptor. These conclusions follow the actual complete
wrapper/dispatcher reads and project memberships, not search absence alone.

SDK blob IDs, checked before every harness build:

| File in `4D Plugin API/` | Git blob |
|---|---|
| `4DPluginAPI.c` | `62f745e91d81ef2c3c901c656ea8e175d3b173e8` |
| `4DPluginAPI.h` | `528bf1d2552df08d6a8b1bc95011a9b72934ad5c` |
| `4DPluginAPI.def` | `5bbdb82fb724b6f8e307feb01d9f158b802de89d` |
| `EntryPoints.h` | `da428d5f6ca23b0a30371c7288a01d29aa3c466a` |
| `Flags.h` | `7a5a35539f27430a3fd9cb711d6b789051316471` |
| `PrivateTypes.h` | `a3098cb662cdbc43ba574fdef72a059c4c6d1cd4` |
| `PublicTypes.h` | `9ae20b001bff9128f690fff3e6c3f873b6238333` |

Application/wrapper baseline blobs:

| File | Git blob |
|---|---|
| `4DPlugin-Trash.cpp` | `4509902367e1fc84159af396b2e7f29839b53a29` |
| `4DPlugin-Trash.h` | `d77137235c425f79de03063906c39fd77378669a` |
| `support/4DPlugin-JSON.cpp` | `a5122d68e881476b97332a6a4f40a7b3e3adeb3f` |
| `support/4DPlugin-JSON.h` | `80aa1f1332655244c4ad4ff09197cb38dcd63912` |
| `Classes/ARRAY_TEXT.cpp` | `534855e0959020ce9a66cef08f8434799c174495` |
| `Classes/ARRAY_TEXT.h` | `cf8a458fc8519b824c726a59d346c44d4c26e845` |
| `Classes/C_BLOB.cpp` | `27bea3172ce90e4da339991c9b318a71ea8609b2` |
| `Classes/C_BLOB.h` | `d181534e5a2e9bd46de492a040c1538ed81bf45a` |
| `Classes/C_LONGINT.cpp` | `005b83a4aec2c1466b8b219cb27ab845eb58de91` |
| `Classes/C_LONGINT.h` | `71e24df633aed7155cba1ffddc07d6a1dfc12fa9` |
| `Classes/C_TEXT.cpp` | `45ea6432dff52b681a5c6a7cea58ad5fc15df66b` |
| `Classes/C_TEXT.h` | `6967390757db569ffcd3cfea8696bf14045cc748` |
| `Classes/C_TYPES.h` | `972cf3f597a8c21881a8c9aa69bc1699455be373` |

## Exact SDK contracts and changes

At baseline SDK lines 4030/4254-4259, CreateVariable(Unistring) creates an empty
owner and SetStringVariable shallow-overwrites it. SetPictureVariable has the
same descriptor-assignment shape (4280). The helper variables now start
Undefined, avoiding an allocation that would be lost; their final ClearVariable
still releases the transferred input. Object/collection variable creation
initializes null refs, so their setter cleanup is intentionally retained.

GetObjectProperty (636-651) and GetCollectionElement (586-600) explicitly return
variables to clear. The application has no collection-element getter wrapper.
The five object-property getters now capture error immediately, inspect and
clear only successful values, and preserve owning matching object/collection
handoffs. A failed result can be unspecified, including raw fType 0, which
UnifyVarKind (1713) normalizes to Unistring; it must not be read or cleared.

Stringify now calls the same OB Copy command directly so it can capture errors
before the SDK convenience getter loses access to the returned descriptor.
Successful non-object copies are cleared; failed unspecified values are not
adopted. The stringify error is captured before independent argument cleanup,
and only successful results are inspected/cleared. Existing failure outputs
remain unchanged: text output is untouched, boolean/number/reference getters
return their established false/zero/null values; ob_get_a still reports property
existence. These helpers have no error-reporting channel and cleanup can reset
the SDK last error. This remains an observability gap, not a new success result,
throw, worker exception, or claim that host errors are reported.

SetStringInArray (4933-4947) accepts Unicode kind, non-null data, index
0..fNbElements inclusive, and non-null descriptor pointer. It disposes the old
element then shallow-assigns the incoming owned descriptor. Rejection retains
caller ownership. Getter (4738-4755) is borrowed; copying it before replacement
is mandatory. Existing wrapper success-path transfer behavior is unchanged.
Scalar SetUnistring (289-299) forwards content to the engine instead; no blanket
disposal removal was applied.

ARRAY_TEXT and C_BLOB are now noncopyable, matching C_TEXT's existing local
prior art. Four explicit baseline copy/assignment probes establish heap
use-after-free; no production copy call is claimed. CBytes parameter replacement
now allocates/fills before disposing the previous owner. The named allocation
failure case proves the old handle, bytes, capacity, and error survive.

## Isolation, cases, and reproduction

Run only:

```sh
python3 .github/tests/test_ownership.py
python3 .github/tests/test_repository_contract.py
```

The final harness compiles the complete pinned SDK, all four unchanged/edited
actual wrapper implementations, the entire actual JSON helper and Trash
dispatcher. It uses ASan/UBSan, with alignment checks excluded for the packed SDK
ABI structures; LeakSanitizer is disabled on macOS and explicit string, picture,
handle, object/collection reference, and synthetic URL owner balances must
instead all reach zero.

Forced-include stubs replace NSURL, NSFileManager, NSWorkspace, CFURL path
functions, and SDK CGContext transforms **before compilation**. They operate
only on in-memory text/objects. Unexpected selectors and engine calls fail
closed. `nm -u` gates the executable before dispatch: only listed Foundation
classes are permitted; real URL/graphics/UI/system/filesystem/network imports
are rejected. No file path fixtures, discovery, real deletion, moving,
recycling, trash access, UI, or platform operation is used.

The Windows dispatcher/worker branch also receives a syntax-only compilation
with trap-only Win32/RPC/thread/mapping/wait/SHFileOperation declarations.
This is not Windows SDK compilation, Windows UTF-16 wchar_t validation, native
ABI testing, worker execution, or a reproduced Windows/4D crash. All actual
macOS consumers compile; dormant classes are compiled too.

ASCII, BMP, supplementary surrogate pairs, and empty values run with allocated
and null engine empties. Assertions check exact UTF-16 units, UTF-8 byte content,
allocation lengths, terminator boundary, independent scalar/array copies,
allocator churn, repeated replacement, shrink/clear, accepted index zero,
rejected kind/index/data/descriptor, successful getter mismatches, owning
reference handoffs, missing/null inputs, and error-poison/raw-kind0 failures.
Actual selector 1 covers synchronous path output, error string, async synthetic
handoff, invalid URL, no resulting URL, and existing exception output.

The same final harness is run against actual baseline sources extracted with
`git archive cf110218c82d39540e598258b1fe53379300e60a` (no additional checkout)
using `OWNERSHIP_SOURCE_ROOT`; baseline-only unsafe copy probes additionally
set `OWNERSHIP_BASELINE_COPY=1`.

| Group | Common cases | Baseline failures | Fixed failures |
|---|---:|---:|---:|
| String/picture setters | 33 | 17 | 0 |
| Five property getters | 80 | 47 | 0 |
| Stringify/copy success, mismatch, failure | 23 | 10 | 0 |
| Actual dispatcher, all wrappers, array/scalar controls, handle failure, traits | 35 | 7 | 0 |
| Total common | 171 | 81 | 0 |
| Baseline-only unsafe copy/assignment | 4 | 4 ASan heap-use-after-free | Not compiled on fixed noncopyable types |

Baseline: 175 runs, 90 passed, 85 failed. Fixed: 171 passed, zero failed.
Named failures include `unreleased string owner`, `unreleased picture owner`,
`unreleased object/collection owner`, `caller handle released after failed
allocation`, invalid-kind UBSan, `reading failed/non-string variable`, and all
four unsafe copies. Controls include null-empty setters and matching owning
object/collection handoffs; a null-empty pass does not excuse allocated-empty
leaks. Immutable local logs are retained in the session artifacts, not binaries
or tracked test output.

## Limits and non-release boundary

Array SDK allocation/resize failure preservation is **not certified**:
PA_ResizeArray (4566-4603) updates fNbElements even when SetHandleSize fails,
and shrink cleanup can precede resize success. ARRAY_TEXT::toParamAtIndex
assumes a valid resized destination; under failed growth, relying on the
updated count could permit an out-of-capacity insertion. Safely changing that
contract requires separate SDK/host work; SDK and ABI headers are protected and
unchanged. The harness asserts low-level allocation/resize failure predicates,
not wrapper OOM safety. Failed string allocation/unspecified host metadata,
arbitrary C++ allocation exceptions inside helpers, and opaque prebuilt
libraries also remain outside the verified contract.

All existing workflows/scripts and project phases were read before validation.
Build and Bump version are manual-only. Xcode Deployment has signing,
packaging, and notarization/upload effects and its launch action targets real
4D; no project build, launch, release, tag, signing, notarization, publication,
manual dispatch, workflow rerun, or dependency installation is performed.
The new Ownership workflow is unsigned, read-only, path-filtered, push-only on
the exact audit branch and master; it runs only this host, not release scripts.
The preservation test allows only the four explicitly corrected native files
in addition to its pre-existing version/release exceptions. Existing release
workflows, project/manifest/version metadata, SDK implementations/ABI headers,
and binaries remain untouched.

Harness structure was informed by immutable
`miyako/4d-plugin-text-input-service-v2@186128786086252548b5218d29d0335eec0f85bb`;
Trash-specific dispatch, platform intercepts, copy failures, and counts above
were obtained here, not copied from another project's results.
