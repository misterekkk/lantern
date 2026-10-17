// "Search Lantern": Lantern's results in Raycast's window, in the order the daemon decides. ↩ opens at the line or
// moment, ⌘↩ shows in Finder, ⌘Y Quick Look; the choice is recorded so Lantern keeps learning.
import { Action, ActionPanel, Icon, LaunchProps, List, getPreferenceValues, open } from "@raycast/api";
import { usePromise } from "@raycast/utils";
import path from "node:path";
import { useState } from "react";
import {
  AppHit,
  DefinitionHit,
  FileHit,
  NotRunning,
  SearchReply,
  SettingHit,
  clock,
  openAt,
  opened,
  reveal,
  search,
  shortPath,
  thumbnail,
} from "./lantern";

interface Preferences {
  includeApps: boolean;
}

// As a Fallback Command (Raycast settings → Fallback Commands) it opens with the text typed in Raycast's root search.
export default function Command(props: LaunchProps) {
  const { includeApps } = getPreferenceValues<Preferences>();
  const [query, setQuery] = useState(props.fallbackText ?? "");
  const [category, setCategory] = useState("");
  const { data, isLoading, error } = usePromise(
    async (q: string, c: string) => (q.trim() ? search(q.trim(), c || undefined) : undefined),
    [query, category],
  );

  if (error instanceof NotRunning) {
    return (
      <List searchText={query} onSearchTextChange={setQuery} throttle>
        <List.EmptyView
          icon={Icon.ExclamationMark}
          title="Lantern is not running"
          description="Start the Lantern app; its background daemon answers these searches."
          actions={
            <ActionPanel>
              <Action title="Open Lantern" icon={Icon.AppWindow} onAction={() => open("/Applications/Lantern.app")} />
            </ActionPanel>
          }
        />
      </List>
    );
  }

  return (
    <List
      isLoading={isLoading}
      searchText={query}
      onSearchTextChange={setQuery}
      throttle
      isShowingDetail={!!data && hasFiles(data)}
      searchBarPlaceholder="Search Lantern by what is inside"
      searchBarAccessory={
        <List.Dropdown tooltip="Kind" storeValue onChange={setCategory}>
          {CATEGORIES.map(([value, title, icon]) => (
            <List.Dropdown.Item key={value} value={value} title={title} icon={icon} />
          ))}
        </List.Dropdown>
      }
    >
      {!query.trim() ? (
        <List.EmptyView icon="icon.png" title="Search your Mac by what is inside" description="Files, code, photos and recordings, found by meaning." />
      ) : error ? (
        <List.EmptyView icon={Icon.Warning} title="Lantern could not search" description={String(error.message)} />
      ) : data ? (
        sections(data, includeApps)
      ) : null}
    </List>
  );
}

/* The panel's filters (search/hybrid.py CATEGORIES) that apply to what this extension shows. */
const CATEGORIES: [string, string, Icon][] = [
  ["", "All Kinds", Icon.MagnifyingGlass],
  ["code", "Code", Icon.Code],
  ["docs", "Documents", Icon.Document],
  ["pdf", "PDFs", Icon.Document],
  ["photos", "Photos", Icon.Image],
  ["media", "Recordings and Videos", Icon.Video],
];

function hasFiles(r: SearchReply): boolean {
  return r.results.length + r.photos.length + r.definitions.length > 0;
}

function sections(r: SearchReply, includeApps: boolean) {
  const order = [...r.order, "definitions", "files", "photos"].filter((name, i, all) => all.indexOf(name) === i);
  return order.map((name) => {
    switch (name) {
      case "definitions":
        return r.definitions.length ? (
          <List.Section key={name} title="Definitions">
            {r.definitions.map((d) => (
              <DefinitionItem key={`${d.path}:${d.line}`} d={d} eventId={r.event_id} />
            ))}
          </List.Section>
        ) : null;
      case "files":
        return r.results.length ? (
          <List.Section key={name} title="Files">
            {r.results.map((h) => (
              <FileItem key={h.chunk_id} h={h} eventId={r.event_id} />
            ))}
          </List.Section>
        ) : null;
      case "photos":
        return r.photos.length ? (
          <List.Section key={name} title="Photos">
            {r.photos.map((h) => (
              <FileItem key={h.chunk_id} h={h} eventId={r.event_id} photo />
            ))}
          </List.Section>
        ) : null;
      case "apps":
        return includeApps && r.apps.length ? (
          <List.Section key={name} title="Applications">
            {r.apps.map((a) => (
              <AppItem key={a.path} a={a} eventId={r.event_id} />
            ))}
          </List.Section>
        ) : null;
      case "settings":
        return includeApps && r.settings.length ? (
          <List.Section key={name} title="System Settings">
            {r.settings.map((s) => (
              <SettingItem key={s.key} s={s} eventId={r.event_id} />
            ))}
          </List.Section>
        ) : null;
      default:
        return null; // music, answers, dictionary: the Lantern panel has them; Raycast has its own
    }
  });
}

/** The id the Lantern app gives the same row (LanternCore SearchResult.id), so its history stays one. */
function fileKey(h: FileHit): string {
  return `${h.kind}|${h.chunk_id}|${h.start_line ?? -1}|${h.path}|`;
}

function where(h: FileHit): string {
  if (h.start_ms != null) return clock(h.start_ms);
  if (h.page != null) return `page ${h.page}`;
  if (h.start_line != null) return h.end_line && h.end_line !== h.start_line ? `lines ${h.start_line}–${h.end_line}` : `line ${h.start_line}`;
  return "";
}

/* Never read from the user's files: Raycast reading files in Downloads or Documents would make macOS ask for access
   to those folders, and permissions cannot be passed on from Lantern. Icons by kind, the preview text the daemon
   sends, and thumbnails the daemon writes to its own cache (thumbnail() in lantern.ts). */
const KIND_ICONS: Record<string, Icon> = {
  code: Icon.Code,
  pdf: Icon.Document,
  image: Icon.Image,
  ocr: Icon.Image,
  audio: Icon.Microphone,
  transcript: Icon.Microphone,
  frame: Icon.Video,
  screen: Icon.Video,
};
const VIDEO = new Set([".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"]);

function kindIcon(h: FileHit, photo: boolean): Icon {
  if (photo) return Icon.Image;
  if (h.start_ms != null && VIDEO.has(path.extname(h.path).toLowerCase())) return Icon.Video;
  return KIND_ICONS[h.kind] ?? Icon.Document;
}

function FileItem({ h, eventId, photo = false }: { h: FileHit; eventId: number | null; photo?: boolean }) {
  const at = where(h);
  const video = h.start_ms != null && VIDEO.has(path.extname(h.path).toLowerCase());
  const { data: thumb } = usePromise(
    async (file: string, ms: number | null, wanted: boolean) => (wanted ? thumbnail(file, ms) : null),
    [h.path, h.start_ms, photo || video],
    { onError: () => undefined }, // no thumbnail (an older daemon, an unreadable file): the kind icon stays
  );
  const text = h.preview.trim();
  const body = text
    ? h.kind === "code"
      ? "```\n" + text + "\n```"
      : text.replace(/([*_`#>[\]])/g, "\\$1")
    : h.start_ms != null
      ? `_A recording. ↩ plays it from ${clock(h.start_ms)}._`
      : photo
        ? "_A photo found by what is in it._"
        : " ";
  const markdown = thumb ? `![](${encodeURI(`file://${thumb}`)})\n\n${text ? body : ""}` : body;
  return (
    <List.Item
      title={path.basename(h.path)}
      subtitle={at}
      icon={thumb ? { source: thumb } : kindIcon(h, photo)}
      quickLook={{ path: h.path, name: path.basename(h.path) }}
      detail={
        <List.Item.Detail
          markdown={markdown}
          metadata={
            <List.Item.Detail.Metadata>
              <List.Item.Detail.Metadata.Label title="Folder" text={shortPath(path.dirname(h.path))} />
              {at ? <List.Item.Detail.Metadata.Label title="Where" text={at} /> : null}
              {h.symbol ? <List.Item.Detail.Metadata.Label title="In" text={h.symbol} /> : null}
            </List.Item.Detail.Metadata>
          }
        />
      }
      actions={
        <ActionPanel>
          <Action
            title={h.start_ms != null ? "Play from Here" : h.start_line != null ? "Open at Line" : "Open"}
            icon={h.start_ms != null ? Icon.Play : Icon.ArrowRight}
            onAction={() => {
              opened(eventId, h.chunk_id, fileKey(h));
              openAt(h.path, h.start_line, h.start_ms);
            }}
          />
          <Action
            title="Show in Finder"
            icon={Icon.Finder}
            shortcut={{ modifiers: ["cmd"], key: "return" }}
            onAction={() => {
              opened(eventId, h.chunk_id, fileKey(h));
              reveal(h.path);
            }}
          />
          <Action.ToggleQuickLook shortcut={{ modifiers: ["cmd"], key: "y" }} />
          <Action.CopyToClipboard title="Copy Path" content={h.path} shortcut={{ modifiers: ["cmd", "shift"], key: "c" }} />
        </ActionPanel>
      }
    />
  );
}

function DefinitionItem({ d, eventId }: { d: DefinitionHit; eventId: number | null }) {
  return (
    <List.Item
      title={d.parent ? `${d.parent}.${d.name}` : d.name}
      subtitle={`line ${d.line}`}
      icon={Icon.Code}
      quickLook={{ path: d.path, name: path.basename(d.path) }}
      detail={
        <List.Item.Detail
          markdown={"```\n" + d.signature + "\n```"}
          metadata={
            <List.Item.Detail.Metadata>
              <List.Item.Detail.Metadata.Label title="File" text={shortPath(d.path)} />
              <List.Item.Detail.Metadata.Label title="Kind" text={d.kind} />
              <List.Item.Detail.Metadata.Label title="Line" text={String(d.line)} />
            </List.Item.Detail.Metadata>
          }
        />
      }
      actions={
        <ActionPanel>
          <Action
            title="Open at Line"
            icon={Icon.ArrowRight}
            onAction={() => {
              opened(eventId, d.chunk_id);
              openAt(d.path, d.line, null);
            }}
          />
          <Action title="Show in Finder" icon={Icon.Finder} shortcut={{ modifiers: ["cmd"], key: "return" }} onAction={() => reveal(d.path)} />
          <Action.ToggleQuickLook shortcut={{ modifiers: ["cmd"], key: "y" }} />
          <Action.CopyToClipboard title="Copy Path" content={d.path} shortcut={{ modifiers: ["cmd", "shift"], key: "c" }} />
        </ActionPanel>
      }
    />
  );
}

function AppItem({ a, eventId }: { a: AppHit; eventId: number | null }) {
  return (
    <List.Item
      title={a.name}
      icon={{ fileIcon: a.path }} // apps live in /Applications: no folder permission involved
      actions={
        <ActionPanel>
          <Action
            title="Open"
            icon={Icon.AppWindow}
            onAction={() => {
              opened(eventId, 0, `app|0|-1|${a.path}|`); // the app's row id: both learn from the same choices
              open(a.path);
            }}
          />
          <Action title="Show in Finder" icon={Icon.Finder} shortcut={{ modifiers: ["cmd"], key: "return" }} onAction={() => reveal(a.path)} />
        </ActionPanel>
      }
    />
  );
}

function SettingItem({ s, eventId }: { s: SettingHit; eventId: number | null }) {
  return (
    <List.Item
      title={s.title}
      subtitle={s.is_pane ? "System Settings" : `System Settings › ${s.pane}`}
      icon={Icon.Gear}
      actions={
        <ActionPanel>
          <Action
            title="Open"
            icon={Icon.Gear}
            onAction={() => {
              opened(eventId, 0, `setting|0|-1|${s.url}|${s.title}`);
              open(s.url);
            }}
          />
        </ActionPanel>
      }
    />
  );
}
