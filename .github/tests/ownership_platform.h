#import <Cocoa/Cocoa.h>

@interface OwnershipURL : NSObject {
    NSString *_text;
}
- (id)initWithString:(NSString *)text;
- (NSString *)absoluteString;
@end
@interface OwnershipFileManager : NSObject
+ (id)defaultManager;
- (BOOL)trashItemAtURL:(OwnershipURL *)url resultingItemURL:(OwnershipURL **)result error:(NSError **)error;
@end
@interface OwnershipWorkspace : NSObject
+ (id)sharedWorkspace;
- (void)recycleURLs:(NSArray *)urls completionHandler:(id)handler;
@end
CFURLRef OwnershipCreateURL(CFAllocatorRef, CFStringRef, CFURLPathStyle, Boolean);
CFStringRef OwnershipCopyPath(CFURLRef, CFURLPathStyle);
void OwnershipScale(CGContextRef, CGFloat, CGFloat);
void OwnershipTranslate(CGContextRef, CGFloat, CGFloat);

#define NSURL OwnershipURL
#define NSFileManager OwnershipFileManager
#define NSWorkspace OwnershipWorkspace
#define CFURLCreateWithFileSystemPath OwnershipCreateURL
#define CFURLCopyFileSystemPath OwnershipCopyPath
#define CGContextScaleCTM OwnershipScale
#define CGContextTranslateCTM OwnershipTranslate
