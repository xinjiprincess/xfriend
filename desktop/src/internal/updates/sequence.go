package updates

import (
	"errors"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func SequencePath(stateDir string) string {
	return filepath.Join(stateDir, "updates", "accepted-sequence.txt")
}

// ReadSequence fails closed on a damaged value instead of silently accepting
// older signed releases again.
func ReadSequence(stateDir string) (uint64, error) {
	data, err := os.ReadFile(SequencePath(stateDir))
	if os.IsNotExist(err) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	value, err := strconv.ParseUint(strings.TrimSpace(string(data)), 10, 64)
	if err != nil {
		return 0, errors.New("invalid update sequence state")
	}
	return value, nil
}

func StoreSequence(stateDir string, sequence uint64) error {
	previous, err := ReadSequence(stateDir)
	if err != nil {
		return err
	}
	if sequence < previous {
		return errors.New("release sequence moved backwards")
	}
	path := SequencePath(stateDir)
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	temporary := path + ".tmp"
	if err := os.WriteFile(temporary, []byte(strconv.FormatUint(sequence, 10)), 0600); err != nil {
		return err
	}
	return os.Rename(temporary, path)
}
