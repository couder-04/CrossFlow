# CrossSight dashboard

Operator UI for the ANPR control room. Next.js 15, React 19, Tailwind 3, deck.gl 9, MapLibre 4.

```bash
cd apps/dashboard
npx pnpm@9 dev
```

## Keyboard shortcuts

| Shortcut | Where | Action |
| --- | --- | --- |
| `⌘K` / `Ctrl+K` | Any page | Open the command palette |
| `Esc` | Palette, wall focus, alert detail | Close |
| `\` | Any page | Toggle the incident tray |
| `↑` `↓` | Alerts (no input focused) | Move selection across Active, In review, then Closed today |
| `A` | Alerts | Acknowledge the selected alert |
| `D` | Alerts | Open the dispatch dialog |
| `R` | Alerts | Open the resolve dialog |
| `V` | Alerts | Center the map on the alert camera |
| `Enter` | Alerts | Open the detail pane |
| `←` `→` | Live map scrubber | Step one minute (up to 15 minutes back) |

## `localStorage` key `crosssight:prefs`

```ts
{
  sidebarCollapsed: boolean;          // default false
  autoOpenTray?: boolean;             // default true for operator/admin, false for analyst
  audio: "off" | "alerts-only" | "all"; // default "alerts-only"
  density: "comfortable" | "compact"; // default "comfortable"
  liveSidebar: { reads: boolean; alerts: boolean }; // default collapsed
}
```

Audio cues stay silent until the first pointer or key event after the page loads. `alerts-only` plays critical and high alert cues. `all` also plays a short plate tick on a focused wall camera.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_VERSION` | Injected from `package.json` at build time and shown in the sidebar |
| `NEXT_PUBLIC_APP_ENV` | `dev` shows demo credentials on the login page. Production builds (`APP_ENV=prod` or `NODE_ENV=production`) omit them |
| `NEXT_PUBLIC_ENABLE_TIME_SCRUB` | Set to `true` to refetch live heatmap and segments with `at=` when the scrubber leaves LIVE. Otherwise the control shows a warning toast |
| `NEXT_PUBLIC_API_URL` / `NEXT_PUBLIC_WS_URL` | Existing API and websocket endpoints |
| `NEXT_PUBLIC_MAP_STYLE_URL` | MapLibre style |

Drop a still frame at `public/login-bg.jpg` for the login backdrop. If the file is missing, the page stays on the solid surface color.
