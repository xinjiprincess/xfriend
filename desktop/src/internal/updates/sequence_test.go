package updates

import "testing"

func TestSequencePersistsAndRejectsRollback(t *testing.T) {
	state := t.TempDir()
	if previous, err := ReadSequence(state); err != nil || previous != 0 {
		t.Fatalf("empty: %d %v", previous, err)
	}
	if err := StoreSequence(state, 5); err != nil {
		t.Fatal(err)
	}
	if err := StoreSequence(state, 6); err != nil {
		t.Fatal(err)
	}
	if err := StoreSequence(state, 4); err == nil {
		t.Fatal("accepted sequence rollback")
	}
	if previous, err := ReadSequence(state); err != nil || previous != 6 {
		t.Fatalf("persisted: %d %v", previous, err)
	}
}
