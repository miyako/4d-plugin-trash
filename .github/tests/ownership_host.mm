// Actual SDK/wrappers/dispatcher; only the engine and platform boundaries are synthetic.
#include "4DPlugin-Trash.h"
#include "ARRAY_TEXT.h"
#include "C_BLOB.h"
#include "C_LONGINT.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"
#include <codecvt>
#include <cstring>
#include <locale>
#include <type_traits>

static std::map<PA_Unichar*, size_t> strings;
static std::map<PA_Picture, int> pictures;
static std::map<PA_Handle, size_t> handles;
struct Reference {
    std::map<CUTF16String, PA_Variable> properties;
    int owners = 1;
    bool collection;
};
static std::map<void*, Reference*> refs;
static bool nullEmpty, failHandle, failResize, missing;
static std::string kind;
static CUTF16String expected;
static int urlOwners, trashCalls, recycleCalls, pathCalls;

static void check(bool valid, const char* message)
{
    if (!valid) { std::cerr << message << "\n"; std::exit(1); }
}
static CUTF16String utf16(const char* text)
{
    std::wstring_convert<std::codecvt_utf8_utf16<char16_t>, char16_t> codec;
    auto u = codec.from_bytes(text);
    return CUTF16String(reinterpret_cast<const PA_Unichar*>(u.data()), u.size());
}
static PA_Unistring makeString(const PA_Unichar* text)
{
    size_t units = 0;
    while (text && text[units]) ++units;
    PA_Unistring v = {};
    if (units || !nullEmpty) {
        v.fString = new PA_Unichar[units + 1];
        if (units) std::memcpy(v.fString, text, units * sizeof(PA_Unichar));
        v.fString[units] = 0; v.fLength = units;
        strings[v.fString] = units;
    }
    return v;
}
static void disposeString(PA_Unistring v)
{
    if (!v.fString) { check(!v.fLength, "null nonempty string"); return; }
    check(strings.count(v.fString), "duplicate/unowned string disposal");
    check(strings.at(v.fString) == static_cast<size_t>(v.fLength), "allocation length");
    strings.erase(v.fString); delete[] v.fString;
}
static void textEquals(PA_Unistring v, const CUTF16String& wanted)
{
    check(v.fLength == static_cast<PA_long32>(wanted.size()), "exact UTF16 units");
    check(v.fString || wanted.empty(), "missing string");
    if (v.fString) {
        check(strings.count(v.fString), "read after release");
        check(v.fString[v.fLength] == 0, "terminator boundary");
        check(CUTF16String(v.fString, v.fLength) == wanted, "exact text");
    }
}
static PA_Picture makePicture()
{ PA_Picture p = new unsigned char(42); pictures[p] = 1; return p; }
static void clear(PA_Variable*);
static Reference* makeRef(bool collection)
{ auto r = new Reference; r->collection = collection; refs[r] = r; return r; }
static void releaseRef(void* p)
{
    if (!p) return;
    check(refs.count(p), "release dead reference");
    auto r = refs.at(p); check(r->owners > 0, "reference balance");
    if (--r->owners) return;
    for (auto& pair : r->properties) clear(&pair.second);
    refs.erase(p); delete r;
}
static PA_Variable clone(PA_Variable v)
{
    if (v.fType == eVK_Unistring) v.uValue.fString = makeString(v.uValue.fString.fString);
    else if (v.fType == eVK_Picture) {
        check(pictures.count(v.uValue.fPicture), "copy dead picture"); ++pictures[v.uValue.fPicture];
    } else if (v.fType == eVK_Object || v.fType == eVK_Collection) {
        void* p = v.fType == eVK_Object ? v.uValue.fObject : v.uValue.fCollection;
        if (p) { check(refs.count(p), "copy dead reference"); ++refs.at(p)->owners; }
    }
    return v;
}
static void clear(PA_Variable* v)
{
    switch (v->fType) {
    case eVK_Unistring: disposeString(v->uValue.fString); break;
    case eVK_Picture:
        if (auto p = v->uValue.fPicture) {
            check(pictures.count(p), "release dead picture");
            if (--pictures[p] == 0) { pictures.erase(p); delete static_cast<unsigned char*>(p); }
        }
        break;
    case eVK_Object: releaseRef(v->uValue.fObject); break;
    case eVK_Collection: releaseRef(v->uValue.fCollection); break;
    case eVK_ArrayUnicode: {
        auto h = v->uValue.fArray.fData; check(handles.count(h), "clear dead array");
        auto values = reinterpret_cast<PA_Unistring*>(*h);
        for (PA_long32 i = 0; i <= v->uValue.fArray.fNbElements; ++i) disposeString(values[i]);
        std::free(*h); delete h; handles.erase(h); break;
    }
    case eVK_Undefined: case static_cast<char>(eVK_Null): case eVK_Boolean: case eVK_Real: case eVK_Longint: break;
    default: check(false, "clear unspecified failed/raw-kind0 return");
    }
    v->fType = eVK_Undefined; v->fFiller = 0;
}
static PA_Variable resultValue()
{
    auto v = PA_CreateVariable(eVK_Undefined);
    if (kind == "string") { auto s = makeString(expected.c_str()); PA_SetStringVariable(&v, &s); }
    else if (kind == "picture") PA_SetPictureVariable(&v, makePicture());
    else if (kind == "object") PA_SetObjectVariable(&v, makeRef(false));
    else if (kind == "collection") PA_SetCollectionVariable(&v, makeRef(true));
    else if (kind == "boolean") PA_SetBooleanVariable(&v, true);
    else if (kind == "real") PA_SetRealVariable(&v, 3.25);
    else check(false, "unknown result kind");
    return v;
}
static void failedResult(PA_Variable* v)
{ std::memset(v, kind == "error-poison" ? 0xa5 : 0, sizeof(*v)); }
static void host(short selector, EngineBlock* eb)
{
    switch (selector) {
    case EX_CREATE_UNISTRING: eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar*>(eb->fHandle)); break;
    case EX_DISPOSE_UNISTRING: disposeString(eb->fUniString1); break;
    case EX_SET_UNISTRING: {
        auto replacement = makeString(reinterpret_cast<PA_Unichar*>(eb->fHandle));
        disposeString(eb->fUniString1); eb->fUniString1 = replacement; break;
    }
    case EX_VARIABLE_TO_STRING: {
        auto v = reinterpret_cast<PA_Variable*>(eb->fHandle);
        check(v->fType == eVK_Unistring, "reading failed/non-string variable");
        textEquals(v->uValue.fString, expected); eb->fUniString1 = v->uValue.fString; break;
    }
    case EX_CREATE_PICTURE:
        check(!eb->fParam1, "unexpected picture bytes"); eb->fPicture = makePicture(); break;
    case EX_CLEAR_VARIABLE: clear(static_cast<PA_Variable*>(eb->fPtr1)); break;
    case EX_GET_INFORMATION: eb->fParam2 = 0x00001630; break;
    case EX_SET_OBJ_VALUE: {
        check(refs.count(eb->fPtr1), "set dead object");
        check(eb->fShort == eVK_Object, "unexpected collection setter");
        auto r = refs.at(eb->fPtr1); auto value = clone(*static_cast<PA_Variable*>(eb->fPtr2));
        auto key = CUTF16String(eb->fUniString1.fString, eb->fUniString1.fLength);
        auto it = r->properties.find(key);
        if (it != r->properties.end()) clear(&it->second);
        r->properties[key] = value; break;
    }
    case EX_GET_OBJ_VALUE: {
        check(refs.count(eb->fPtr1), "get dead object");
        check(eb->fShort == eVK_Object, "unexpected collection getter");
        auto v = static_cast<PA_Variable*>(eb->fPtr2);
        if (kind.find("error-") == 0) { failedResult(v); eb->fError = eER_IncompatibleTypes; }
        else *v = resultValue();
        break;
    }
    case EX_HANDLE_MANAGER: {
        auto h = eb->fHandle;
        switch (eb->fParam1) {
        case 1:
            if (failHandle) { eb->fHandle = nullptr; eb->fError = eER_IncompatibleTypes; break; }
            h = new char*; *h = static_cast<char*>(std::calloc(1, std::max<sLONG_PTR>(1, eb->fParam2)));
            check(*h, "test allocation failed"); handles[h] = eb->fParam2; eb->fHandle = h; break;
        case 2: check(handles.count(h), "dispose dead handle"); std::free(*h); delete h; handles.erase(h); break;
        case 3:
            check(handles.count(h), "resize dead handle");
            if (failResize) { eb->fError = eER_IncompatibleTypes; break; }
            *h = static_cast<char*>(std::realloc(*h, std::max<sLONG_PTR>(1, eb->fParam2)));
            check(*h, "test resize failed"); handles[h] = eb->fParam2; break;
        case 4: check(handles.count(h), "size dead handle"); eb->fParam2 = handles[h]; break;
        case 5: check(handles.count(h), "lock dead handle"); eb->fParam3 = reinterpret_cast<sLONG_PTR>(*h); break;
        case 6: check(handles.count(h), "unlock dead handle"); break;
        default: check(false, "unexpected handle operation");
        }
        break;
    }
    case EX_EXECUTE_COMMAND_BY_ID: {
        auto args = static_cast<PA_Variable**>(eb->fPtr1); auto v = static_cast<PA_Variable*>(eb->fPtr2);
        check(v->fType == eVK_Undefined, "command result initialization");
        if (eb->fParam1 == 1471) PA_SetObjectVariable(v, makeRef(false));
        else if (eb->fParam1 == 1231) {
            check(refs.count(args[0]->uValue.fObject), "existence on dead object");
            textEquals(args[1]->uValue.fString, utf16("key")); PA_SetBooleanVariable(v, !missing);
        } else if (eb->fParam1 == 1225) {
            if (kind == "error-copy") { failedResult(v); eb->fError = eER_IncompatibleTypes; }
            else if (kind.find("copy-") == 0) {
                auto saved = kind; kind = kind.substr(5); *v = resultValue(); kind = saved;
            } else {
                check(refs.count(args[0]->uValue.fObject), "copy dead object");
                PA_SetObjectVariable(v, makeRef(false)); if (kind == "error-handle") failHandle = true;
            }
        } else if (eb->fParam1 == 1217) {
            check(refs.count(args[0]->uValue.fObject), "stringify dead argument");
            if (kind.find("error-") == 0) { failedResult(v); eb->fError = eER_IncompatibleTypes; }
            else *v = resultValue();
        } else check(false, "unexpected command");
        break;
    }
    default: std::cerr << "unexpected engine selector " << selector << "\n"; std::exit(1);
    }
}

@implementation OwnershipURL
- (void)doesNotRecognizeSelector:(SEL)selector { check(false, "unexpected URL selector"); }
- (id)initWithString:(NSString *)text
{ self = [super init]; if (self) { _text = [text copy]; ++urlOwners; } return self; }
- (NSString *)absoluteString { return _text; }
- (void)dealloc { [_text release]; --urlOwners; [super dealloc]; }
@end
@implementation OwnershipFileManager
- (void)doesNotRecognizeSelector:(SEL)selector { check(false, "unexpected file-manager selector"); }
+ (id)defaultManager { return [[[self alloc] init] autorelease]; }
- (BOOL)trashItemAtURL:(OwnershipURL *)url resultingItemURL:(OwnershipURL **)result error:(NSError **)error
{
    check(url && result && error, "synthetic trash arguments"); ++trashCalls;
    if (kind == "throw") throw 42;
    if (kind == "failure") {
        *error = [NSError errorWithDomain:@"synthetic" code:42
                                userInfo:@{NSLocalizedDescriptionKey: [url absoluteString]}];
        return NO;
    }
    *result = kind == "no-result" ? nil : url; return YES;
}
@end
@implementation OwnershipWorkspace
- (void)doesNotRecognizeSelector:(SEL)selector { check(false, "unexpected workspace selector"); }
+ (id)sharedWorkspace { return [[[self alloc] init] autorelease]; }
- (void)recycleURLs:(NSArray *)urls completionHandler:(id)handler
{ check([urls count] == 1 && !handler, "synthetic recycle arguments"); ++recycleCalls; }
@end
CFURLRef OwnershipCreateURL(CFAllocatorRef, CFStringRef text, CFURLPathStyle, Boolean)
{
    ++pathCalls;
    return kind == "invalid-url" ? nullptr :
        reinterpret_cast<CFURLRef>([[OwnershipURL alloc] initWithString:(NSString *)text]);
}
CFStringRef OwnershipCopyPath(CFURLRef url, CFURLPathStyle)
{
    ++pathCalls; check(url, "synthetic path argument");
    return reinterpret_cast<CFStringRef>([[(OwnershipURL *)url absoluteString] copy]);
}
void OwnershipScale(CGContextRef, CGFloat, CGFloat) { check(false, "unexpected drawing"); }
void OwnershipTranslate(CGContextRef, CGFloat, CGFloat) { check(false, "unexpected drawing"); }

static void arrayCase()
{
    auto ar = PA_CreateVariable(eVK_ArrayUnicode); PA_ResizeArray(&ar, 3);
    for (int repeat = 0; repeat < 20; ++repeat) {
        for (int i = 0; i <= 3; ++i) {
            auto s = makeString(expected.c_str()); PA_SetStringInArray(ar, i, &s);
            textEquals(PA_GetStringInArray(ar, i), expected);
        }
        auto borrowed = PA_GetStringInArray(ar, 1); auto copy = PA_CreateUnistring(borrowed.fString);
        PA_SetStringInArray(ar, 1, &copy);
        for (int i = 0; i < 10; ++i) { auto s = makeString(expected.c_str()); PA_DisposeUnistring(&s); }
        textEquals(PA_GetStringInArray(ar, 1), expected);
    }
    PA_ResizeArray(&ar, 1);
    auto owned = makeString(expected.c_str()); auto wrong = PA_CreateVariable(eVK_Longint);
    PA_SetStringInArray(wrong, 0, &owned); PA_SetStringInArray(ar, -1, &owned); PA_SetStringInArray(ar, 2, &owned);
    auto noData = ar; noData.uValue.fArray.fData = nullptr;
    PA_SetStringInArray(noData, 0, &owned); PA_SetStringInArray(ar, 0, nullptr);
    textEquals(owned, expected); PA_DisposeUnistring(&owned); PA_ClearVariable(&ar);
}
static void wrapperCase(const std::string& name)
{
#ifdef OWNERSHIP_BASELINE_COPY
    if (name == "copy-array") { ARRAY_TEXT a; { ARRAY_TEXT b = a; } a.getSize(); return; }
    if (name == "assign-array") { ARRAY_TEXT a, b; b = a; return; }
    if (name == "copy-blob") { C_BLOB a; { C_BLOB b = a; } a.getBytesLength(); return; }
    if (name == "assign-blob") { C_BLOB a, b; b = a; return; }
#endif
    if (name == "copy-traits") {
        check(!std::is_copy_constructible<ARRAY_TEXT>::value && !std::is_copy_assignable<ARRAY_TEXT>::value, "ARRAY_TEXT shallow copy");
        check(!std::is_copy_constructible<C_BLOB>::value && !std::is_copy_assignable<C_BLOB>::value, "C_BLOB shallow copy");
        check(!std::is_copy_constructible<C_TEXT>::value && !std::is_copy_assignable<C_TEXT>::value, "C_TEXT noncopy prior art");
        return;
    }
    C_TEXT text; text.setUTF16String(expected.c_str(), expected.size());
    CUTF16String copy; text.copyUTF16String(&copy); check(copy == expected, "scalar copy");
    CUTF8String bytes; text.copyUTF8String(&bytes); C_TEXT converted; converted.setUTF8String(&bytes);
    converted.copyUTF16String(&copy); check(copy == expected, "scalar UTF8/UTF16 units");
    auto scalar = makeString(nullptr); void* scalars[] = {&scalar};
    text.toParamAtIndex(reinterpret_cast<PackagePtr>(scalars), 1); textEquals(scalar, expected);
    C_TEXT readText; readText.fromParamAtIndex(reinterpret_cast<PackagePtr>(scalars), 1);
    PA_DisposeUnistring(&scalar); readText.copyUTF16String(&copy); check(copy == expected, "scalar borrowed copy");
    ARRAY_TEXT a; a.appendUTF8String(&bytes); a.copyUTF16StringAtIndex(&copy, 0);
    check(copy == expected, "array UTF8/UTF16 units");
    auto ar = PA_CreateVariable(eVK_Undefined); void* args[] = {&ar};
    a.toParamAtIndex(reinterpret_cast<PackagePtr>(args), 1);
    ARRAY_TEXT b; b.fromParamAtIndex(reinterpret_cast<PackagePtr>(args), 1); PA_ClearVariable(&ar);
    b.copyUTF16StringAtIndex(&copy, 0); check(copy == expected, "borrowed copied before mutation");
    C_LONGINT n; n.setIntValue(42); PA_long32 number = 0; void* ints[] = {&number};
    n.toParamAtIndex(reinterpret_cast<PackagePtr>(ints), 1); check(number == 42, "integer wrapper");
    const uint8_t data[] = {1, 2, 3}; C_BLOB blob; blob.setBytes(data, 3);
    PA_Handle output = nullptr; void* blobs[] = {&output};
    blob.toParamAtIndex(reinterpret_cast<PackagePtr>(blobs), 1); C_BLOB read;
    read.fromParamAtIndex(reinterpret_cast<PackagePtr>(blobs), 1); PA_DisposeHandle(output);
    check(read.getBytesLength() == 3 && !std::memcmp(read.getBytesPtr(), data, 3), "blob independent copy");
}
static void dispatchCase()
{
    auto scalar = makeString(expected.c_str());
    PA_long32 mode = kind == "async" ? 1 : 0;
    void* args[] = {&scalar, &mode}; void* output = nullptr;
    PluginBlock p = {}; p.fParameters = args; p.fResult = &output;
    PluginMain(1, &p);
    check(refs.count(output), "actual dispatcher returns owning object");
    auto object = refs.at(output);
    if (kind == "async") check(recycleCalls == 1 && !trashCalls && object->properties.empty(), "async behavior");
    else if (kind == "invalid-url" || kind == "throw" || kind == "failure") {
        check(!object->properties.at(utf16("success")).uValue.fBoolean, "failure output");
        double code = kind == "invalid-url" ? 90001 : kind == "throw" ? 90004 : 42;
        check(object->properties.at(utf16("error")).uValue.fReal == code, "failure code");
        if (kind == "failure") check(object->properties.at(utf16("errorString")).fType == eVK_Unistring, "error text");
    } else {
        check(trashCalls == 1 && !recycleCalls, "synchronous synthetic boundary");
        check(object->properties.at(utf16("success")).uValue.fBoolean, "success output");
        if (kind == "no-result") check(!object->properties.count(utf16("path")), "no result path");
        else textEquals(object->properties.at(utf16("path")).uValue.fString, expected);
    }
    PA_DisposeObject(output); PA_DisposeUnistring(&scalar);
    PluginMain(0, nullptr); PluginMain(999, nullptr);
}
static void helperCase(const std::string& name, const char* raw)
{
    auto object = makeRef(false);
    if (name == "set-narrow") ob_set_s(object, "key", raw);
    else if (name == "set-wide") ob_set_s(object, L"key", raw);
    else if (name == "set-utf16") ob_set_a(object, L"key", &expected);
    else if (name == "set-wchars") {
        std::wstring_convert<std::codecvt_utf8<wchar_t>> codec;
        auto wide = codec.from_bytes(raw); ob_set_a(object, L"key", wide.c_str());
    } else if (name == "set-picture") {
        ob_set_p(object, L"key", makePicture());
        check(pictures.at(object->properties.at(utf16("key")).uValue.fPicture) == 1, "property retains picture");
    } else if (name == "get-a") {
        CUTF8String value(reinterpret_cast<const uint8_t*>("unchanged"));
        check(ob_get_a(object, L"key", &value) == !missing, "existence boolean");
        auto wanted = kind == "string" && !missing ? raw : "unchanged";
        check(value == CUTF8String(reinterpret_cast<const uint8_t*>(wanted)), "getter output");
    } else if (name == "get-b") check(ob_get_b(object, L"key") == (kind == "boolean" && !missing), "boolean getter");
    else if (name == "get-n") check(ob_get_n(object, L"key") == (kind == "real" && !missing ? 3.25 : 0.0), "real getter");
    else if (name == "get-c" || name == "get-o") {
        void* value = name == "get-c" ? ob_get_c(object, L"key") : ob_get_o(object, L"key");
        check((value != nullptr) == (kind == (name == "get-c" ? "collection" : "object") && !missing), "owning handoff");
        if (value) {
            check(refs.count(value), "premature reference release");
            if (name == "get-c") PA_DisposeCollection(value); else PA_DisposeObject(value);
        }
    } else if (name == "stringify") {
        CUTF8String value(reinterpret_cast<const uint8_t*>("unchanged"));
        if (kind == "error-first-handle") failHandle = true;
        ob_stringify(object, &value);
        check(value == CUTF8String(reinterpret_cast<const uint8_t*>(kind == "string" ? raw : "unchanged")), "stringify output");
    } else if (name == "values") {
        ob_set_i(object, L"key", 42); check(object->properties.at(utf16("key")).uValue.fLongint == 42, "integer setter");
        ob_set_n(object, L"key", 3.25); ob_set_n(object, "key", 3.25);
        check(object->properties.at(utf16("key")).uValue.fReal == 3.25, "real setters");
        ob_set_b(object, L"key", true); check(object->properties.at(utf16("key")).uValue.fBoolean, "boolean setter");
        auto child = makeRef(false); ob_set_o(object, L"key", child); check(refs.at(child)->owners == 1, "object transfer");
        child = makeRef(false); ob_set_o(object, "key", child); check(refs.at(child)->owners == 1, "narrow object transfer");
        auto c = makeRef(true); ob_set_c(object, L"key", c); check(refs.at(c)->owners == 1, "collection transfer");
        ob_set_0(object, L"key"); ob_set_0(object, "key");
        check(object->properties.at(utf16("key")).fType == static_cast<char>(eVK_Null), "null setters");
        check(ob_is_defined(object, L"key"), "defined property");
    } else check(false, "unknown helper");
    if (name.find("set-") == 0 && name != "set-picture") textEquals(object->properties.at(utf16("key")).uValue.fString, expected);
    PA_DisposeObject(object);
}
int main(int argc, char** argv)
{
    @autoreleasepool {
        check(argc == 5, "arguments"); gCall4D = host;
        const std::string name = argv[1]; expected = utf16(argv[2]); nullEmpty = std::string(argv[3]) == "null"; kind = argv[4];
        if (name == "array") arrayCase();
        else if (name == "dispatch") dispatchCase();
        else if (name == "wrappers" || name == "copy-traits" || name.find("copy-") == 0 || name.find("assign-") == 0) wrapperCase(name);
        else if (name == "missing") {
            missing = true; for (const char* getter : {"get-a", "get-b", "get-n", "get-c", "get-o"}) helperCase(getter, argv[2]);
        } else if (name == "null-controls") {
            CUTF8String value(reinterpret_cast<const uint8_t*>("unchanged"));
            check(!ob_get_a(nullptr, L"key", &value) && !ob_get_b(nullptr, L"key") &&
                  ob_get_n(nullptr, L"key") == 0.0 && !ob_get_c(nullptr, L"key") &&
                  !ob_get_o(nullptr, L"key") && !ob_is_defined(nullptr, L"key"), "null getters");
            ob_set_s(nullptr, "key", "text"); ob_set_s(nullptr, L"key", "text");
            ob_set_a(nullptr, L"key", L"text"); ob_set_a(nullptr, L"key", &expected);
            ob_set_p(nullptr, L"key", nullptr); ob_set_o(nullptr, L"key", nullptr); ob_set_c(nullptr, L"key", nullptr);
            ob_stringify(nullptr, &value);
            check(value == CUTF8String(reinterpret_cast<const uint8_t*>("unchanged")), "null output");
        } else if (name == "sdk-failures") {
            failHandle = true; auto h = PA_NewHandle(8); check(!h && PA_GetLastError() != eER_NoErr, "allocation failure predicate");
            failHandle = false; h = PA_NewHandle(8); failResize = true;
            check(!PA_SetHandleSize(h, 16) && PA_GetLastError() != eER_NoErr && handles.at(h) == 8, "resize failure predicate");
            failResize = false; PA_DisposeHandle(h);
        } else if (name == "blob-allocation-failure") {
            auto h = PA_NewHandle(3); auto original = h;
            std::memcpy(PA_LockHandle(h), "old", 3); PA_UnlockHandle(h);
            const uint8_t bytes[] = {1, 2, 3}; C_BLOB blob; blob.setBytes(bytes, 3);
            void* args[] = {&h}; failHandle = true;
            blob.toParamAtIndex(reinterpret_cast<PackagePtr>(args), 1);
            auto error = PA_GetLastError();
            check(h == original && handles.count(h), "caller handle released after failed allocation");
            check(error != eER_NoErr && handles.at(h) == 3, "allocation error and capacity preserved");
            check(!std::memcmp(PA_LockHandle(h), "old", 3), "old bytes preserved");
            PA_UnlockHandle(h); failHandle = false; PA_DisposeHandle(h);
        } else helperCase(name, argv[2]);
        check(strings.empty(), "unreleased string owner"); check(pictures.empty(), "unreleased picture owner");
        check(handles.empty(), "unreleased handle owner"); check(refs.empty(), "unreleased object/collection owner");
    }
    check(!urlOwners, "platform autorelease balance");
    std::cout << "strings=0 pictures=0 handles=0 objects=0 collections=0 platform=0\n";
}
