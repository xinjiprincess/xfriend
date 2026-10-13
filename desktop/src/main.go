package main

import (
	"context"
	"embed"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sync"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

//go:embed assets/*
var assets embed.FS

const trayDoubleClick = 400 * time.Millisecond

// App is the Wails service bound to the shell UI.
type App struct {
	app              *application.App
	window           *application.WebviewWindow
	settingsWindow   *application.WebviewWindow
	assistantWindow  *application.WebviewWindow
	settingsOnce     sync.Once
	assistantOnce    sync.Once
	tray             *application.SystemTray
	voiceMu          sync.Mutex
	voiceCmd         *exec.Cmd
	assistantBase    string
	assistantSession string
	store            *settingsStore
	sleep            *sleepGuard
	cmd              *exec.Cmd
	cmdDone          <-chan struct{}
	mu               sync.Mutex
	quitting         bool
	startupErr       error
	desktop          *desktopServer

	trayClickMu    sync.Mutex
	lastTrayClick  time.Time
	trayClickTimer *time.Timer
}

func (a *App) ServiceName() string { return "desktop" }

func (a *App) ServiceStartup(context.Context, application.ServiceOptions) error { return nil }

func (a *App) ServiceShutdown() error {
	a.StopAssistantVoice()
	a.sleep.stop()
	a.mu.Lock()
	a.quitting = true
	cmd := a.cmd
	done := a.cmdDone
	a.cmd = nil
	a.mu.Unlock()
	stopOctop(cmd)
	if done != nil {
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			if cmd != nil && cmd.Process != nil {
				_ = cmd.Process.Kill()
			}
			<-done
		}
	}
	a.desktop.close()
	return nil
}

func (a *App) GetSettings() Settings {
	return a.store.get()
}

func (a *App) GetSettingsStatus() (Settings, error) {
	s := a.store.get()
	if a.app == nil {
		return s, nil
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return s, fmt.Errorf("read autostart status: %w", err)
	}
	if s.Autostart != status.Enabled {
		s.Autostart = status.Enabled
		if err := a.store.save(s); err != nil {
			return s, err
		}
	}
	return s, nil
}

func (a *App) SaveSettings(next Settings) (Settings, error) {
	cur, err := a.GetSettingsStatus()
	if err != nil {
		return cur, err
	}
	autostart, err := a.setAutostart(next.Autostart)
	if err != nil {
		return cur, err
	}
	if err := a.sleep.set(next.PreventSleep); err != nil {
		if _, rollbackErr := a.setAutostart(cur.Autostart); rollbackErr != nil {
			log.Printf("rollback autostart after sleep prevention failure: %v", rollbackErr)
		}
		return cur, err
	}
	next.Autostart = autostart
	if err := a.store.save(next); err != nil {
		return cur, err
	}
	saved := a.store.get()
	a.applyDashboardPrefs(saved)
	return saved, nil
}

func (a *App) ShowMain() {
	a.showWindow()
}

func (a *App) HideSettings() {
	if window := a.settingsWindowRef(); window != nil {
		window.Hide()
	}
}

func (a *App) Quit() {
	a.requestQuit()
}

func (a *App) setAutostart(on bool) (bool, error) {
	if a.app == nil {
		return false, fmt.Errorf("autostart is unavailable before the application starts")
	}
	if on {
		if err := a.app.Autostart.Enable(); err != nil {
			return false, fmt.Errorf("enable autostart: %w", err)
		}
	} else if err := a.app.Autostart.Disable(); err != nil {
		return false, fmt.Errorf("disable autostart: %w", err)
	}
	status, err := a.app.Autostart.Status()
	if err != nil {
		return false, fmt.Errorf("read autostart status: %w", err)
	}
	if status.Enabled != on {
		return status.Enabled, fmt.Errorf("autostart state did not update")
	}
	return status.Enabled, nil
}

func (a *App) applyDashboardPrefs(s Settings) {
	if a.window == nil {
		return
	}
	js := fmt.Sprintf(
		`(function(){try{const locale=%s;if(localStorage.getItem('octop:ui-locale')!==locale){localStorage.setItem('octop:ui-locale',locale);location.reload();}}catch(e){}})();`,
		jsonString(string(s.Locale)),
	)
	a.window.ExecJS(js)
}

func jsonString(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func (a *App) setStatus(msg string) {
	if a.app == nil {
		return
	}
	a.app.Event.Emit("desktop:status", msg)
}

func (a *App) boot() {
	if a.startupErr != nil {
		a.setStatus(a.startupErr.Error())
		return
	}
	locale := LocaleZH
	if a.store != nil {
		locale = a.store.get().Locale
	}
	if url := os.Getenv("OCTOP_DESKTOP_URL"); url != "" {
		a.setStatus(desktopText(locale, copyStatusConnecting))
		if err := waitHealth(locale, url, 60*time.Second); err != nil {
			a.setStatus(err.Error())
			return
		}
		a.showDashboard(url)
		return
	}
	a.setStatus(desktopText(locale, copyStatusCheckingRuntime))
	fail := func(phase string, err error) {
		a.setStatus(err.Error())
		a.desktop.update("failed", phase, err.Error())
	}
	if err := ensurePortableWithDashboard(locale, func(message string) {
		a.setStatus(message)
		a.desktop.update("starting", "preparing_runtime", message)
	}, a.desktop); err != nil {
		fail("preparing_runtime", err)
		return
	}
	root := portableDir()
	if err := a.desktop.verifyRuntime(root); err != nil {
		fail("runtime_version", err)
		return
	}
	if a.desktop.cloud != nil {
		a.bootCloud(root, locale)
		return
	}
	port, err := availableLocalPort(0)
	if err != nil {
		fail("starting_service", err)
		return
	}
	base := dashboardURL(port)
	if err := a.desktop.setBackend(base); err != nil {
		fail("starting_service", err)
		return
	}
	a.desktop.update("starting", "starting_service", desktopText(locale, copyStatusStartingService))
	processExited := make(chan struct{})
	a.mu.Lock()
	if a.quitting {
		a.mu.Unlock()
		return
	}
	stopOctop(a.cmd)
	cmd, err := startOctop(root, port)
	a.cmd = cmd
	if err == nil {
		a.cmdDone = processExited
	}
	a.mu.Unlock()
	if err != nil {
		fail("starting_service", err)
		return
	}
	processDone := make(chan error, 1)
	logPath := ""
	if runtime.GOOS == "windows" {
		logPath = serviceLogPath()
	}
	go func() {
		defer close(processExited)
		err := cmd.Wait()
		processDone <- err
		a.mu.Lock()
		quitting := a.quitting
		a.mu.Unlock()
		if !quitting {
			if err == nil {
				err = fmt.Errorf("exit code 0")
			}
			fail("service_exited", withServiceLog(locale, fmt.Errorf("%s", desktopText(locale, copyServiceExited, err)), logPath))
		}
	}()
	a.setStatus(desktopText(locale, copyStatusStartingService))
	if err := waitHealthForProcess(locale, base, 5*time.Minute, processDone, logPath); err != nil {
		fail("starting_service", err)
		return
	}
	a.desktop.mu.Lock()
	if a.desktop.status.State == "failed" {
		a.desktop.mu.Unlock()
		return
	}
	a.desktop.status.State, a.desktop.status.Phase, a.desktop.status.Message = "ready", "core_ready", ""
	a.desktop.mu.Unlock()
	if root == filepath.Join(octopHome(), "portable") {
		_ = os.RemoveAll(root + ".previous")
	} else {
		if err := os.WriteFile(installedRuntimeVersionPath(), []byte(portableVersion(root)), 0600); err != nil {
			log.Printf("record installed runtime version: %v", err)
		}
	}
	a.showDashboard(a.desktop.base)
	monitorCtx, cancelMonitor := context.WithCancel(context.Background())
	go func() {
		<-processExited
		cancelMonitor()
	}()
	go a.desktop.monitorBackend(monitorCtx, base, 2*time.Second)
}

func (a *App) showDashboard(base string) {
	if a.window == nil {
		return
	}
	if target, err := url.Parse(base); err == nil {
		query := target.Query()
		query.Set("desktopLocale", string(a.store.get().Locale))
		target.RawQuery = query.Encode()
		base = target.String()
	}
	// The local SPA is already interactive while Python starts; do not reload it
	// when the health check completes (that would discard unsent input).
	if a.desktop == nil {
		a.window.SetURL(base)
	}
	a.mu.Lock()
	a.assistantBase = base
	a.mu.Unlock()
	if window := a.assistantWindowRef(); window != nil {
		window.ExecJS("window.dispatchEvent(new CustomEvent('assistant:base',{detail:" + jsonString(base) + "}));")
	}
	if a.store.get().AssistantVisible {
		a.ShowAssistant()
	}
	a.scheduleDragOverlay()
	s := a.store.get()
	go func() {
		time.Sleep(800 * time.Millisecond)
		a.applyDashboardPrefs(s)
	}()
	a.setStatus(desktopText(s.Locale, copyStatusReady))
}

func (a *App) hideToTray() {
	if a.window == nil {
		return
	}
	a.window.Hide()
}

func (a *App) showWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsMinimised() {
		a.window.UnMinimise()
	}
	a.window.Show()
	a.window.Focus()
}

func (a *App) toggleMainWindow() {
	if a.window == nil {
		return
	}
	if a.window.IsVisible() && !a.window.IsMinimised() {
		a.hideToTray()
		return
	}
	a.showWindow()
}

func (a *App) onTrayLeftClick() {
	a.trayClickMu.Lock()
	defer a.trayClickMu.Unlock()
	if a.trayClickTimer != nil {
		a.trayClickTimer.Stop()
		a.trayClickTimer = nil
	}
	now := time.Now()
	if !a.lastTrayClick.IsZero() && now.Sub(a.lastTrayClick) < trayDoubleClick {
		a.lastTrayClick = time.Time{}
		go a.toggleMainWindow()
		return
	}
	a.lastTrayClick = now
	a.trayClickTimer = time.AfterFunc(trayDoubleClick, func() {
		a.trayClickMu.Lock()
		a.trayClickTimer = nil
		a.trayClickMu.Unlock()
		a.showWindow()
	})
}

func (a *App) installDragOverlay() {
	if a.window == nil {
		return
	}
	a.window.ExecJS(dragOverlayJS())
}

func (a *App) scheduleDragOverlay() {
	go func() {
		for range 40 {
			time.Sleep(250 * time.Millisecond)
			a.installDragOverlay()
			a.installExternalLinks()
		}
	}()
}

func (a *App) requestQuit() {
	a.mu.Lock()
	a.quitting = true
	a.mu.Unlock()
	if a.app != nil {
		a.app.Quit()
	}
}

func main() {
	instanceID, err := desktopInstanceID(octopHome())
	if err != nil {
		log.Fatal(err)
	}
	profilePath := filepath.Join(octopHome(), "webview2")
	store := &settingsStore{}
	api := &App{
		store: store,
		sleep: &sleepGuard{},
	}

	app := application.New(application.Options{
		Name:        "Singu Xfriend",
		Description: "Singu Xfriend desktop",
		SingleInstance: &application.SingleInstanceOptions{
			UniqueID: instanceID,
			// This IPC is deliberately limited to showing the existing window.
			// Never execute arguments or open URLs supplied by another process.
			OnSecondInstanceLaunch: func(_ application.SecondInstanceData) { api.showWindow() },
		},
		Services: []application.Service{
			application.NewService(api),
		},
		RawMessageHandler: api.handleAssistantMessage,
		Assets: application.AssetOptions{
			Handler: application.BundledAssetFileServer(assets),
		},
		Windows: application.WindowsOptions{
			DisableQuitOnLastWindowClosed: true,
			WebviewUserDataPath:           profilePath,
		},
		Linux: application.LinuxOptions{
			DisableQuitOnLastWindowClosed: true,
		},
		Mac: application.MacOptions{
			ApplicationShouldTerminateAfterLastWindowClosed: false,
		},
	})
	api.app = app
	// Wails acquires the single-instance lock inside New, before any migration,
	// settings write, WebView profile creation or Python process can take place.
	dataLock, startupErr := lockDesktopData(instanceID)
	if startupErr == nil {
		defer dataLock.Close()
		startupErr = migrateDesktopHome()
	}
	if startupErr != nil {
		app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(_ *application.ApplicationEvent) {
			app.Dialog.Error().SetTitle("Singu Xfriend 启动失败").SetMessage(startupErr.Error()).Show()
			app.Quit()
		})
		if runErr := app.Run(); runErr != nil {
			log.Print(runErr)
		}
		return
	}
	store.cur = loadSettings()
	windowURL := "/login"
	if os.Getenv("OCTOP_DESKTOP_URL") == "" {
		files, assetsErr := fs.Sub(dashboardAssets, "ui")
		if assetsErr == nil {
			api.desktop, assetsErr = newDesktopServer(files)
		}
		if assetsErr == nil {
			assetsErr = api.desktop.configureCloud()
		}
		if assetsErr == nil {
			assetsErr = api.desktop.listen(store.get().Port)
		}
		api.startupErr = assetsErr
		if assetsErr == nil {
			windowURL = api.desktop.base + "login?desktopLocale=" + url.QueryEscape(string(store.get().Locale))
			if api.desktop.cloud != nil {
				state := api.desktop.cloud.status().State
				initial := "login"
				if state == "authenticated" || state == "restoring" {
					initial = "chat"
				}
				windowURL = api.desktop.base + initial + "?desktopLocale=" + url.QueryEscape(string(store.get().Locale))
			}
			api.assistantBase = api.desktop.base
		}
	}
	attachOpenURLEventListener(app, api.OpenExternal)
	app.Event.OnApplicationEvent(events.Common.ApplicationStarted, func(_ *application.ApplicationEvent) {
		applyAppIcon(app)
	})

	win := app.Window.NewWithOptions(application.WebviewWindowOptions{
		Title:                "Singu Xfriend",
		Width:                1200,
		Height:               800,
		URL:                  windowURL,
		Frameless:            true,
		AllowSimpleEventEmit: true,
		BackgroundColour:     application.NewRGB(247, 248, 250),
	})
	api.window = win
	app.Event.On("desktop:toggle-maximise", func(_ *application.CustomEvent) {
		win.ToggleMaximise()
	})
	app.Event.On("desktop:minimise", func(_ *application.CustomEvent) {
		win.Minimise()
	})
	app.Event.On("desktop:close", func(_ *application.CustomEvent) {
		api.hideToTray()
	})
	var bootOnce sync.Once
	installDragOverlay := func(_ *application.WindowEvent) {
		api.scheduleDragOverlay()
		// Navigate only after the main WebView controller has initialized.
		bootOnce.Do(func() { go api.boot() })
		settings := api.settingsWindowRef()
		if win.IsVisible() && (settings == nil || !settings.IsVisible()) {
			win.Focus()
		}
	}
	win.OnWindowEvent(events.Mac.WebViewDidFinishNavigation, installDragOverlay)
	win.OnWindowEvent(events.Windows.WebViewNavigationCompleted, installDragOverlay)
	win.OnWindowEvent(events.Linux.WindowLoadFinished, installDragOverlay)
	app.Event.RegisterApplicationEventHook(events.Mac.ApplicationShouldHandleReopen, func(event *application.ApplicationEvent) {
		event.Cancel()
		restoreMainAfterDockClick(api.window, api.settingsWindowRef())
	})

	win.RegisterHook(events.Common.WindowClosing, func(e *application.WindowEvent) {
		api.mu.Lock()
		quit := api.quitting
		api.mu.Unlock()
		if quit {
			return
		}
		e.Cancel()
		api.hideToTray()
	})
	win.OnWindowEvent(events.Common.WindowMinimise, func(_ *application.WindowEvent) {
		if api.store.get().MinimizeToTray {
			api.hideToTray()
		}
	})
	tray := app.SystemTray.New()
	api.tray = tray
	applyTrayIcon(tray)
	tray.SetTooltip("Singu Xfriend")
	showSettings := func() { api.ShowSettings() }
	if trayLeftClickShowsSettings(runtime.GOOS) {
		tray.OnClick(showSettings)
	} else {
		tray.OnClick(func() { api.onTrayLeftClick() })
	}
	tray.OnRightClick(showSettings)

	if _, err := api.setAutostart(store.get().Autostart); err != nil {
		log.Printf("sync autostart: %v", err)
	}
	if err := api.sleep.set(store.get().PreventSleep); err != nil {
		log.Printf("enable sleep prevention: %v", err)
	}

	api.scheduleDragOverlay()

	if err := app.Run(); err != nil {
		log.Fatal(err)
	}
}
