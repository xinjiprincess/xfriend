//go:build darwin

package main

/*
#cgo LDFLAGS: -framework Security -framework CoreFoundation
#include <Security/Security.h>
#include <CoreFoundation/CoreFoundation.h>
#include <string.h>

static OSStatus accountKey(unsigned char *out) {
 CFMutableDictionaryRef query = CFDictionaryCreateMutable(NULL, 0, &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
 CFDictionarySetValue(query, kSecClass, kSecClassGenericPassword);
 CFDictionarySetValue(query, kSecAttrService, CFSTR("com.singu.xfriend"));
 CFDictionarySetValue(query, kSecAttrAccount, CFSTR("cloud-account-key-v1"));
 CFDictionarySetValue(query, kSecReturnData, kCFBooleanTrue);
 CFTypeRef result = NULL;
 OSStatus status = SecItemCopyMatching(query, &result);
 if (status == errSecItemNotFound) {
  unsigned char key[32];
  status = SecRandomCopyBytes(kSecRandomDefault, sizeof(key), key);
  if (status == errSecSuccess) {
   CFDataRef data = CFDataCreate(NULL, key, sizeof(key));
   CFDictionaryRemoveValue(query, kSecReturnData);
   CFDictionarySetValue(query, kSecValueData, data);
   status = SecItemAdd(query, NULL);
   CFRelease(data);
   CFDictionaryRemoveValue(query, kSecValueData);
   CFDictionarySetValue(query, kSecReturnData, kCFBooleanTrue);
   if (status == errSecSuccess || status == errSecDuplicateItem) status = SecItemCopyMatching(query, &result);
  }
  memset(key, 0, sizeof(key));
 }
 if (status == errSecSuccess) {
  if (result == NULL || CFGetTypeID(result) != CFDataGetTypeID() || CFDataGetLength((CFDataRef)result) != 32) status = errSecDecode;
  else memcpy(out, CFDataGetBytePtr((CFDataRef)result), 32);
 }
 if (result) CFRelease(result);
 CFRelease(query);
 return status;
}
*/
import "C"

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"fmt"
	"unsafe"
)

func accountCipher() (cipher.AEAD, error) {
	key := make([]byte, 32)
	defer clear(key)
	if status := C.accountKey((*C.uchar)(unsafe.Pointer(&key[0]))); status != 0 {
		return nil, fmt.Errorf("无法访问 macOS 钥匙串（%d）", int(status))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	return cipher.NewGCM(block)
}

func protectAccount(data []byte) ([]byte, error) {
	aead, err := accountCipher()
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err = rand.Read(nonce); err != nil {
		return nil, err
	}
	return aead.Seal(nonce, nonce, data, []byte("xfriend-account-v1")), nil
}

func unprotectAccount(data []byte) ([]byte, error) {
	aead, err := accountCipher()
	if err != nil {
		return nil, err
	}
	if len(data) < aead.NonceSize()+aead.Overhead() {
		return nil, fmt.Errorf("账号存储已损坏")
	}
	return aead.Open(nil, data[:aead.NonceSize()], data[aead.NonceSize():], []byte("xfriend-account-v1"))
}
