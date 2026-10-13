import type { ReactNode } from "react";
import "../pages/Customers/strings";
import {
  Monitor,
  MessageSquareText,
  Timer,
  SlidersHorizontal,
  Waypoints,
  Link2,
  Database,
  Cpu,
  Users as UsersIcon,
  Activity,
  Share2,
  Cloudy,
  Sparkles,
  Puzzle,
  Package,
  Smartphone,
  HardDrive,
  GraduationCap,
  Shield,
  PanelsTopLeft,
} from "lucide-react";
import type { OctopUser } from "../api/modules/auth";
import { navAllowed, userCan } from "../utils/permissions";

export const EXPANDED_WIDTH = 220;
export const COLLAPSED_WIDTH = 56;

const iconSize = 16;
const iconStroke = 1.8;

export interface NavItem {
  key: string;
  path: string;
  icon: ReactNode;
  labelKey: string;
  badge?: string;
}

export interface NavSection {
  /** Stable group id. Omitted for the ungrouped block at the top. */
  id?: string;
  /** Built-in label key. Omitted when {@link title} is set or the block is ungrouped. */
  groupKey?: string;
  /** User-defined group title. Wins over {@link groupKey}. */
  title?: string;
  items: NavItem[];
}

/** Every nav item key the sidebar can show. Keep in sync with `SIDEBAR_NAV_KEYS` in `octop.infra.users.preferences`. */
export const SIDEBAR_NAV_KEYS = [
  "customers",
  "chat",
  "experts",
  "engines",
  "tasks",
  "token-usage",
  "personalization",
  "channels",
  "connectors",
  "skill-packages",
  "knowledge-bases",
  "transfer",
  "phone-connection",
  "bridge",
  "workbench",
  "remote-desktop",
  "acp",
  "assistant-settings",
  "platform-collaboration",
  "admin-users",
  "models",
  "admin-storage",
  "admin-plugins",
  "admin-security",
  "admin-advanced",
] as const;

export const BUILTIN_NAV_GROUP_IDS = ["settings", "control", "admin"] as const;

const BUILTIN_NAV_GROUP_LABEL_KEYS: Record<
  (typeof BUILTIN_NAV_GROUP_IDS)[number],
  string
> = {
  settings: "nav.settings",
  control: "nav.control",
  admin: "nav.admin",
};

export function isBuiltinNavGroupId(id: string): boolean {
  return (BUILTIN_NAV_GROUP_IDS as readonly string[]).includes(id);
}

export function builtinNavGroupLabelKey(id: string): string | null {
  if (!isBuiltinNavGroupId(id)) return null;
  return BUILTIN_NAV_GROUP_LABEL_KEYS[
    id as (typeof BUILTIN_NAV_GROUP_IDS)[number]
  ];
}

export function navSectionLabel(
  section: NavSection,
  translate: (key: string) => string,
): string {
  if (section.title) return section.title;
  if (section.groupKey) return translate(section.groupKey);
  return "";
}

/**
 * Catalog of nav item keys that live under settings / control / admin groups.
 * Permission-independent — used for pane/route helpers; visibility still
 * comes from {@link buildNavSections}.
 */
export const SIDEBAR_GROUPED_NAV_KEYS = [
  "phone-connection",
  "transfer",
  "assistant-settings",
  "platform-collaboration",
  "personalization",
  "channels",
  "connectors",
  "skill-packages",
  "knowledge-bases",
  "bridge",
  "workbench",
  "remote-desktop",
  "acp",
  "admin-users",
  "models",
  "admin-storage",
  "admin-plugins",
  "admin-security",
  "admin-advanced",
  "agent-config",
] as const;

const GROUPED_NAV_KEY_SET = new Set<string>(SIDEBAR_GROUPED_NAV_KEYS);

export function isGroupedNavKey(key: string): boolean {
  return GROUPED_NAV_KEY_SET.has(key);
}

export function buildNavSections(
  user: OctopUser | null,
  opts?: { mobileEnabled?: boolean },
): NavSection[] {
  const sections: NavSection[] = [
    {
      items: [
        {
          key: "customers",
          path: "/customers",
          icon: <UsersIcon size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "customers:title",
        },
        {
          key: "chat",
          path: "/chat",
          icon: <MessageSquareText size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "nav.chat",
        },
        {
          key: "experts",
          path: "/experts",
          icon: <GraduationCap size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "nav.experts",
        },
        {
          key: "tasks",
          path: "/tasks",
          icon: <Timer size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "nav.tasks",
        },
        {
          key: "engines",
          path: "/engines",
          icon: <Cpu size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "engineHub.hubTitle",
        },
        {
          key: "token-usage",
          path: "/token-usage",
          icon: <Activity size={iconSize} strokeWidth={iconStroke} />,
          labelKey: "nav.tokenUsage",
        },
      ],
    },
  ];

  const settingsItems: NavItem[] = [
    {
      key: "phone-connection",
      path: "/settings/phone",
      icon: <Smartphone size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "phoneConnection.title",
    },
    {
      key: "transfer",
      path: "/settings/transfer",
      icon: <Package size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "portableTransfer.title",
    },
    {
      key: "personalization",
      path: "/personalization/skills",
      icon: <Sparkles size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.personalization",
    },
  ];
  if (navAllowed(user, "channels")) {
    settingsItems.push({
      key: "channels",
      path: "/personalization/channels",
      icon: <Waypoints size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.channels",
    });
  }
  if (navAllowed(user, "connectors")) {
    settingsItems.push({
      key: "connectors",
      path: "/connectors",
      icon: <Link2 size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.connectors",
    });
  }
  if (navAllowed(user, "skill-packages")) {
    settingsItems.push({
      key: "skill-packages",
      path: "/skill-packages",
      icon: <Package size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.skillPackages",
    });
  }
  if (navAllowed(user, "knowledge-bases")) {
    settingsItems.push({
      key: "knowledge-bases",
      path: "/knowledge-bases",
      icon: <Database size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.knowledgeBases",
    });
  }
  // User-scoped remote Octop links — always available (like personalization).
  settingsItems.push({
    key: "bridge",
    path: "/bridge",
    icon: <Cloudy size={iconSize} strokeWidth={iconStroke} />,
    labelKey: "nav.bridge",
    badge: "Beta",
  });
  if (settingsItems.length > 0) {
    sections.push({
      id: "settings",
      groupKey: "nav.settings",
      items: settingsItems,
    });
  }

  const controlItems: NavItem[] = [];
  if (navAllowed(user, "workbench")) {
    controlItems.push({
      key: "workbench",
      path: "/workbench",
      icon: <PanelsTopLeft size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.workbench",
    });
  }
  // Server desktop + phone share one nav entry; phone tab also needs host capability.
  if (
    userCan(user, "desktop") ||
    (opts?.mobileEnabled && userCan(user, "mobile"))
  ) {
    controlItems.push({
      key: "remote-desktop",
      path: "/remote-desktop",
      icon: <Monitor size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.remoteDesktop",
    });
  }
  // ACP: no module key this round — admin role only.
  if (navAllowed(user, "acp")) {
    controlItems.push({
      key: "acp",
      path: "/acp",
      icon: <Share2 size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.acp",
    });
    controlItems.push({
      key: "platform-collaboration",
      path: "/platform-collaboration",
      icon: <Share2 size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "platformCollaboration.title",
    });
    controlItems.push({
      key: "assistant-settings",
      path: "/assistant-settings",
      icon: <Sparkles size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "assistantSettings.title",
    });
  }
  if (controlItems.length > 0) {
    sections.push({
      id: "control",
      groupKey: "nav.control",
      items: controlItems,
    });
  }

  const adminItems: NavItem[] = [];
  if (navAllowed(user, "admin-users")) {
    adminItems.push({
      key: "admin-users",
      path: "/admin/users",
      icon: <UsersIcon size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminUsers",
    });
  }
  if (navAllowed(user, "models")) {
    adminItems.push({
      key: "models",
      path: "/admin/models",
      icon: <Cpu size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.models",
    });
  }
  if (navAllowed(user, "admin-storage")) {
    adminItems.push({
      key: "admin-storage",
      path: "/admin/backend",
      icon: <HardDrive size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminStorage",
    });
  }
  if (navAllowed(user, "admin-plugins")) {
    adminItems.push({
      key: "admin-plugins",
      path: "/admin/plugins",
      icon: <Puzzle size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminPlugins",
    });
  }
  if (navAllowed(user, "admin-security")) {
    adminItems.push({
      key: "admin-security",
      path: "/admin/security",
      icon: <Shield size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.security",
    });
  }
  if (navAllowed(user, "admin-advanced")) {
    adminItems.push({
      key: "admin-advanced",
      path: "/admin/advanced",
      icon: <SlidersHorizontal size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminAdvanced",
    });
  }
  if (adminItems.length > 0) {
    sections.push({ id: "admin", groupKey: "nav.admin", items: adminItems });
  }
  return sections;
}
