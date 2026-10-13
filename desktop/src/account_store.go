package main

import (
	"encoding/json"
	"os"
	"path/filepath"
)

type accountStore interface {
	load() ([]byte, error)
	save([]byte) error
}

type protectedAccountStore struct{ path string }

func (s protectedAccountStore) load() ([]byte, error) {
	data, err := os.ReadFile(s.path)
	if err != nil {
		return nil, err
	}
	return unprotectAccount(data)
}

func (s protectedAccountStore) save(data []byte) error {
	encoded, err := protectAccount(data)
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(s.path), 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(filepath.Dir(s.path), ".account-*")
	if err != nil {
		return err
	}
	name := file.Name()
	defer os.Remove(name)
	if err = file.Chmod(0600); err == nil {
		_, err = file.Write(encoded)
	}
	if err == nil {
		err = file.Sync()
	}
	closeErr := file.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(name, s.path)
}

func accountJSON(value any) []byte {
	data, _ := json.Marshal(value)
	return data
}
