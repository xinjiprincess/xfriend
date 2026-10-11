package main

import (
	"strings"
	"testing"

	"github.com/wailsapp/wails/v3/pkg/application"
)

func TestAssistantSessionOrigin(t *testing.T) {
	base := "http://127.0.0.1:18088/?desktopLocale=zh"
	for _, tc := range []struct {
		source, top string
		allowed     bool
	}{
		{"http://127.0.0.1:18088/chat/agent/thread", "http://127.0.0.1:18088/chat/agent/thread", true},
		{"http://127.0.0.1:18089/chat", "http://127.0.0.1:18089/chat", false},
		{"https://example.com", "https://example.com", false},
		{"http://127.0.0.1:18088/embed", "http://127.0.0.1:18088/chat", false},
		{"", "", false},
	} {
		if got := assistantOriginAllowed(base, &application.OriginInfo{Origin: tc.source, TopOrigin: tc.top}); got != tc.allowed {
			t.Fatalf("origin %q: got %v", tc.source, got)
		}
	}
	if assistantOriginAllowed(base, nil) {
		t.Fatal("missing origin accepted")
	}
}

func TestAssistantRejectsInvalidWakeWordBeforeStartingMicrophone(t *testing.T) {
	a := &App{}
	for _, word := range []string{"", " ", "星", strings.Repeat("星", 21)} {
		if a.StartAssistantVoice(word) == nil {
			t.Fatalf("accepted %q", word)
		}
		if a.voiceCmd != nil {
			t.Fatal("invalid word opened microphone")
		}
	}
}

func TestAssistantWakeWordPersistsWithoutEnablingMicrophone(t *testing.T) {
	t.Setenv("XFRIEND_HOME", t.TempDir())
	s := defaultSettings()
	s.WakeWord = "你好小星"
	store := &settingsStore{cur: defaultSettings()}
	if err := store.save(s); err != nil {
		t.Fatal(err)
	}
	if got := loadSettings().WakeWord; got != s.WakeWord {
		t.Fatalf("wake word lost: %q", got)
	}
	app := &App{store: store}
	if app.voiceCmd != nil {
		t.Fatal("loading settings started microphone")
	}
}

func TestAssistantUpdatesWakeWordWithoutOpeningMicrophone(t *testing.T) {
	t.Setenv("XFRIEND_HOME", t.TempDir())
	store := &settingsStore{cur: defaultSettings()}
	app := &App{store: store}
	if err := app.SetAssistantWakeWord("你好小星"); err != nil {
		t.Fatal(err)
	}
	if got := store.get().WakeWord; got != "你好小星" {
		t.Fatalf("wrong word: %q", got)
	}
	if app.voiceCmd != nil {
		t.Fatal("saving word opened microphone")
	}
}
