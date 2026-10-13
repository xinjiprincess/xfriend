package main

import (
	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
	"runtime"
)

const (
	settingsWindowWidth  = 400
	settingsWindowHeight = 560
	// Windows DWM frameless decorations shrink the WebView2 client area, which
	// clips the 28px bottom gap used on macOS.
	settingsWindowWindowsExtraHeight = 24
)

func settingsWindowOuterHeight() int {
	if runtime.GOOS == "windows" {
		return settingsWindowHeight + settingsWindowWindowsExtraHeight
	}
	return settingsWindowHeight
}

func (a *App) settingsWindowRef() *application.WebviewWindow {
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.settingsWindow
}

func (a *App) ensureSettingsWindow() *application.WebviewWindow {
	if a.app == nil {
		return nil
	}
	a.settingsOnce.Do(func() {
		window := a.app.Window.NewWithOptions(application.WebviewWindowOptions{
			Title: "Singu Xfriend 设置", Width: settingsWindowWidth, Height: settingsWindowOuterHeight(),
			URL: "/?settings=1", Hidden: true, Frameless: true, AlwaysOnTop: true, DisableResize: true,
			BackgroundColour: application.NewRGB(255, 255, 255),
			Windows:          application.WindowsWindow{HiddenOnTaskbar: true},
		})
		a.mu.Lock()
		a.settingsWindow = window
		a.mu.Unlock()
		window.RegisterHook(events.Common.WindowClosing, func(event *application.WindowEvent) { event.Cancel(); window.Hide() })
		window.OnWindowEvent(events.Common.WindowLostFocus, func(_ *application.WindowEvent) { window.Hide() })
	})
	return a.settingsWindowRef()
}

func (a *App) ShowSettings() {
	window := a.ensureSettingsWindow()
	if window == nil {
		return
	}
	if a.tray != nil {
		a.tray.AttachWindow(window).WindowOffset(6)
		a.tray.ShowWindow()
	} else {
		window.Show().Focus()
	}
}
