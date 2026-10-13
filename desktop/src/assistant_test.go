package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/wailsapp/wails/v3/pkg/application"
)

func TestLazyAssistantCachesOnlyTrustedSessionWithoutCreatingWindow(t *testing.T) {
	mainWindow := &application.WebviewWindow{}
	app := &App{window: mainWindow, assistantBase: "http://127.0.0.1:18088/"}
	origin := &application.OriginInfo{Origin: "http://127.0.0.1:18088", TopOrigin: "http://127.0.0.1:18088"}
	app.handleAssistantMessage(mainWindow, `{"type":"xfriend:session","token":"session-one"}`, origin)
	if app.assistantSession != "session-one" || app.assistantWindowRef() != nil {
		t.Fatal("session sync must wait in memory without creating the assistant WebView")
	}
	app.handleAssistantMessage(&application.WebviewWindow{}, `{"type":"xfriend:session","token":"wrong-window"}`, origin)
	app.handleAssistantMessage(mainWindow, `{"type":"xfriend:session","token":"wrong-origin"}`, &application.OriginInfo{Origin: "https://example.com", TopOrigin: "https://example.com"})
	if app.assistantSession != "session-one" {
		t.Fatal("untrusted session replaced cached credentials")
	}
	app.handleAssistantMessage(mainWindow, `{"type":"xfriend:session","token":null}`, origin)
	if app.assistantSession != "" {
		t.Fatal("logout did not clear cached session")
	}
}

func TestAssistantVisibilityPersistsWithoutStartingWindowOrMicrophone(t *testing.T) {
	home := t.TempDir()
	t.Setenv("XFRIEND_HOME", home)
	if loadSettings().AssistantVisible {
		t.Fatal("fresh installs must only create the main WebView")
	}
	if err := os.WriteFile(filepath.Join(home, "desktop-settings.json"), []byte(`{"locale":"zh"}`), 0600); err != nil {
		t.Fatal(err)
	}
	if !loadSettings().AssistantVisible {
		t.Fatal("legacy settings must preserve automatic assistant restoration")
	}
	app := &App{store: &settingsStore{cur: loadSettings()}}
	app.setAssistantVisible(false)
	if loadSettings().AssistantVisible {
		t.Fatal("hidden assistant was not remembered")
	}
	app.setAssistantVisible(true)
	if !loadSettings().AssistantVisible {
		t.Fatal("shown assistant was not remembered")
	}
	if app.assistantWindowRef() != nil || app.voiceCmd != nil {
		t.Fatal("saving visibility created a window or opened the microphone")
	}
}

func TestLazyAuxiliaryCallbacksAreSafeBeforeWindowsExist(t *testing.T) {
	app := &App{}
	app.ShowSettings()
	app.HideSettings()
	app.ShowAssistant()
	app.HideAssistant()
	app.ExpandAssistant(true)
	app.assistantVoiceEvent("stopped", "")
	if app.settingsWindowRef() != nil || app.assistantWindowRef() != nil {
		t.Fatal("callbacks created a window without a running application")
	}
}

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
