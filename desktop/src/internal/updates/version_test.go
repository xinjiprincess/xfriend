package updates

import "testing"

func TestNewerVersion(t *testing.T) {
	for _, item := range []struct {
		current, next string
		expected      bool
	}{
		{"1.4.7", "1.4.8", true},
		{"1.4.7", "1.5.0", true},
		{"1.4.7", "1.4.7", false},
		{"1.4.7", "1.4.6", false},
		{"1.10.0", "1.9.9", false},
		{"bad", "1.5.0", false},
	} {
		if actual := NewerVersion(item.current, item.next); actual != item.expected {
			t.Errorf("%s -> %s: got %v", item.current, item.next, actual)
		}
	}
}
