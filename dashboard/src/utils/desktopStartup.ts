/** Set by the desktop's local static server; legacy shells use their own loader. */
export function usesDesktopStartup(): boolean {
  return document.documentElement.dataset.xfriendDesktop === "1";
}
