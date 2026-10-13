//go:build windows

package main

import (
	"golang.org/x/sys/windows"
	"unsafe"
)

func accountCrypt(data []byte, protect bool) ([]byte, error) {
	input := windows.DataBlob{Size: uint32(len(data))}
	if len(data) > 0 {
		input.Data = &data[0]
	}
	var output windows.DataBlob
	var err error
	if protect {
		err = windows.CryptProtectData(&input, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &output)
	} else {
		err = windows.CryptUnprotectData(&input, nil, nil, 0, nil, windows.CRYPTPROTECT_UI_FORBIDDEN, &output)
	}
	if err != nil {
		return nil, err
	}
	defer windows.LocalFree(windows.Handle(unsafe.Pointer(output.Data)))
	return append([]byte(nil), unsafe.Slice(output.Data, output.Size)...), nil
}

func protectAccount(data []byte) ([]byte, error)   { return accountCrypt(data, true) }
func unprotectAccount(data []byte) ([]byte, error) { return accountCrypt(data, false) }
