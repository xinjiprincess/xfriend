package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

func (a *App) ensureAssistantWindow() *application.WebviewWindow {
	if a.app == nil {
		return nil
	}
	a.assistantOnce.Do(func() {
		w := a.app.Window.NewWithOptions(application.WebviewWindowOptions{
			Title: "Singu Xfriend 小星助手", Width: 160, Height: 180,
			URL: "/assistant.html", Hidden: true, Frameless: true,
			AlwaysOnTop: true, DisableResize: true,
			BackgroundColour: application.NewRGB(255, 255, 255),
			Windows:          application.WindowsWindow{HiddenOnTaskbar: true},
		})
		a.mu.Lock()
		a.assistantWindow = w
		a.mu.Unlock()
		w.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
			e.Cancel()
			a.HideAssistant()
		})
		flushSession := func(_ *application.WindowEvent) { a.flushAssistantSession(w) }
		w.OnWindowEvent(events.Windows.WebViewNavigationCompleted, flushSession)
		w.OnWindowEvent(events.Mac.WebViewDidFinishNavigation, flushSession)
		w.OnWindowEvent(events.Linux.WindowLoadFinished, flushSession)
	})
	return a.assistantWindowRef()
}

func (a *App) assistantWindowRef() *application.WebviewWindow {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.assistantWindow
}

func (a *App) flushAssistantSession(window *application.WebviewWindow) {
	if window == nil {
		return
	}
	a.mu.Lock()
	token := a.assistantSession
	a.mu.Unlock()
	window.ExecJS("window.dispatchEvent(new CustomEvent('assistant:session',{detail:" + jsonString(token) + "}));")
}

func (a *App) AssistantBase() string {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.assistantBase
}

func (a *App) handleAssistantMessage(window application.Window, message string, origin *application.OriginInfo) {
	if window != a.window || !assistantOriginAllowed(a.AssistantBase(), origin) {
		return
	}
	var data struct {
		Type  string `json:"type"`
		Token string `json:"token"`
	}
	if len(message) > 32768 || json.Unmarshal([]byte(message), &data) != nil {
		return
	}
	if data.Type == "xfriend:show-assistant" {
		a.ShowAssistant()
		return
	}
	if data.Type != "xfriend:session" {
		return
	}
	// Hold the most recent session only in memory until the lazy assistant loads.
	// Never persist or log credentials, and never create a window for token sync.
	a.mu.Lock()
	a.assistantSession = data.Token
	a.mu.Unlock()
	a.flushAssistantSession(a.assistantWindowRef())
}

func assistantOriginAllowed(base string, origin *application.OriginInfo) bool {
	if origin == nil || origin.Origin == "" || origin.Origin != origin.TopOrigin {
		return false
	}
	expected, err := url.Parse(base)
	if err != nil || expected.Host == "" {
		return false
	}
	source, err := url.Parse(origin.Origin)
	return err == nil && source.Scheme == expected.Scheme && source.Host == expected.Host
}

func (a *App) ShowAssistant() {
	if window := a.ensureAssistantWindow(); window != nil {
		a.setAssistantVisible(true)
		window.Show()
		window.Focus()
	}
}

func (a *App) setAssistantVisible(visible bool) {
	if a.store == nil {
		return
	}
	settings := a.store.get()
	if settings.AssistantVisible == visible {
		return
	}
	settings.AssistantVisible = visible
	if err := a.store.save(settings); err != nil {
		log.Printf("save assistant visibility: %v", err)
	}
}

func (a *App) OpenAssistantSettings() {
	if a.window != nil && a.AssistantBase() != "" {
		if target, err := url.Parse(a.AssistantBase()); err == nil {
			target.Path, target.RawPath = "/assistant-settings", ""
			a.window.SetURL(target.String())
		}
	}
	a.showWindow()
}

func (a *App) OpenAssistantConversation(agent, thread string) {
	base := a.AssistantBase()
	if a.window != nil && base != "" && agent != "" && thread != "" {
		target, err := url.Parse(base)
		if err == nil {
			target.Path = "/chat/" + agent + "/" + thread
			target.RawPath = "/chat/" + url.PathEscape(agent) + "/" + url.PathEscape(thread)
			a.window.SetURL(target.String())
		}
	}
	a.showWindow()
}

func (a *App) HideAssistant() {
	a.StopAssistantVoice()
	a.setAssistantVisible(false)
	if window := a.assistantWindowRef(); window != nil {
		window.Hide()
	}
}

func (a *App) ExpandAssistant(expanded bool) {
	window := a.assistantWindowRef()
	if window == nil {
		return
	}
	if expanded {
		window.SetSize(420, 680)
	} else {
		window.SetSize(160, 180)
	}
}

func (a *App) assistantVoiceEvent(kind, text string) {
	window := a.assistantWindowRef()
	if window == nil {
		return
	}
	data, _ := json.Marshal(map[string]string{"kind": kind, "text": text})
	window.ExecJS("window.dispatchEvent(new CustomEvent('assistant:voice',{detail:" + string(data) + "}));")
}

func (a *App) SetAssistantWakeWord(wakeWord string) error {
	wakeWord = strings.TrimSpace(wakeWord)
	if len([]rune(wakeWord)) < 2 || len([]rune(wakeWord)) > 20 {
		return fmt.Errorf("唤醒词需要 2 到 20 个字")
	}
	s := a.store.get()
	if s.WakeWord == wakeWord {
		return nil
	}
	s.WakeWord = wakeWord
	if err := a.store.save(s); err != nil {
		return err
	}
	a.StopAssistantVoice()
	return nil
}

func (a *App) StartAssistantVoice(wakeWord string) error {
	wakeWord = strings.TrimSpace(wakeWord)
	if len([]rune(wakeWord)) < 2 || len([]rune(wakeWord)) > 20 {
		return fmt.Errorf("唤醒词需要 2 到 20 个字")
	}
	if runtime.GOOS != "windows" {
		return fmt.Errorf("当前离线唤醒仅支持 Windows")
	}
	a.StopAssistantVoice()
	s := a.store.get()
	s.WakeWord = wakeWord
	if err := a.store.save(s); err != nil {
		return err
	}
	script, err := assets.ReadFile("assets/voice.ps1")
	if err != nil {
		return err
	}
	path := filepath.Join(octopHome(), "voice", "recognizer.ps1")
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	if err := os.WriteFile(path, script, 0o644); err != nil {
		return err
	}
	cmd := assistantSpeechCommand(path, wakeWord)
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	a.voiceMu.Lock()
	a.voiceCmd = cmd
	a.voiceMu.Unlock()
	go func() {
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			var event struct {
				Kind string `json:"kind"`
				Text string `json:"text"`
			}
			if json.Unmarshal(scanner.Bytes(), &event) == nil {
				a.voiceMu.Lock()
				current := a.voiceCmd == cmd
				a.voiceMu.Unlock()
				if current {
					a.assistantVoiceEvent(event.Kind, event.Text)
				}
			}
		}
		_ = cmd.Wait()
		a.voiceMu.Lock()
		current := a.voiceCmd == cmd
		if current {
			a.voiceCmd = nil
		}
		a.voiceMu.Unlock()
		if current {
			a.assistantVoiceEvent("stopped", "")
		}
	}()
	return nil
}

func (a *App) StopAssistantVoice() {
	a.voiceMu.Lock()
	cmd := a.voiceCmd
	a.voiceCmd = nil
	a.voiceMu.Unlock()
	if cmd != nil && cmd.Process != nil {
		_ = cmd.Process.Kill()
	}
	a.assistantVoiceEvent("stopped", "")
}
