import { useEffect, useState, useRef } from "react";
import { Capacitor } from "@capacitor/core";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Collaboration from "@tiptap/extension-collaboration";
import CollaborationCursor from "@tiptap/extension-collaboration-cursor";
import * as Y from "yjs";
import { WebsocketProvider } from "y-websocket";
import { useAuthStore } from "@/stores/auth-store";
import { generateColor } from "@/lib/utils";
import {
  Bold,
  Italic,
  Strikethrough,
  List,
  ListOrdered,
  Quote,
  Heading1,
  Heading2,
  Code,
  Minus,
  Loader2,
  Check,
  WifiOff,
  RefreshCw,
} from "lucide-react";
import { cn } from "@/lib/utils";

interface CollaborativeEditorProps {
  documentId: string;
  initialContent?: string;
  onUpdate?: (content: string) => void;
}

// Web builds use a secure WebSocket (wss) whenever the page itself is served
// over https so production never downgrades to cleartext. Native (Capacitor)
// builds can't derive the scheme from the page protocol — the WebView is
// https even in local dev — so they default to plain ws against the Android
// emulator's host-loopback alias (10.0.2.2) or the iOS simulator's localhost,
// matching the api-client's plain-http local-dev default.
// Any real deployment must point VITE_COLLAB_WS_URL at a wss endpoint.
const WEB_WS_SCHEME =
  typeof window !== "undefined" && window.location.protocol === "https:" ? "wss" : "ws";
const NATIVE_WS_SCHEME = "ws";
const NATIVE_WS_HOST = Capacitor.getPlatform() === "android" ? "10.0.2.2" : "localhost";
const COLLAB_WS_URL =
  import.meta.env.VITE_COLLAB_WS_URL ||
  (Capacitor.isNativePlatform()
    ? `${NATIVE_WS_SCHEME}://${NATIVE_WS_HOST}:8085`
    : `${WEB_WS_SCHEME}://localhost:8085`);

export function CollaborativeEditor({ documentId, initialContent, onUpdate }: CollaborativeEditorProps) {
  const { user } = useAuthStore();
  const [ydoc] = useState(() => new Y.Doc());
  const [provider, setProvider] = useState<WebsocketProvider | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<"connected" | "disconnected" | "connecting">("connecting");
  const [isSynced, setIsSynced] = useState(false);
  const [hasLocalChanges, setHasLocalChanges] = useState(false);
  const syncTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const token = typeof window !== "undefined" ? localStorage.getItem("otter_access_token") : null;
    const wsProvider = new WebsocketProvider(
      COLLAB_WS_URL,
      `document-${documentId}`,
      ydoc,
      { params: { token: token || "" } }
    );

    wsProvider.on("status", (event: { status: "connected" | "disconnected" | "connecting" }) => {
      setConnectionStatus(event.status);
    });

    wsProvider.on("sync", (synced: boolean) => {
      setIsSynced(synced);
      if (synced) {
        setHasLocalChanges(false);
      }
    });

    setProvider(wsProvider);

    return () => {
      wsProvider.destroy();
      if (syncTimeoutRef.current) clearTimeout(syncTimeoutRef.current);
    };
  }, [documentId, ydoc]);

  const [contentLoaded, setContentLoaded] = useState(false);
  // Suppress onUpdate callback while we inject saved content to avoid writing it back
  const suppressSaveRef = useRef(false);

  const editor = useEditor(
    {
      extensions: [
        StarterKit.configure({ history: false }),
        Collaboration.configure({ document: ydoc }),
        ...(provider
          ? [
              CollaborationCursor.configure({
                provider,
                user: {
                  name: user?.displayName || "Anonymous",
                  color: generateColor(user?.id || "anon"),
                },
              }),
            ]
          : []),
      ],
      editorProps: {
        attributes: {
          class:
            "prose prose-sm max-w-none focus:outline-none min-h-[500px] px-8 py-6",
        },
      },
      onUpdate: ({ editor: ed }) => {
        if (!suppressSaveRef.current) {
          onUpdate?.(ed.getHTML());
          setHasLocalChanges(true);
          if (syncTimeoutRef.current) clearTimeout(syncTimeoutRef.current);
          syncTimeoutRef.current = setTimeout(() => {
            setHasLocalChanges(false);
          }, 3000);
        }
      },
    },
    [provider]
  );

  // Load saved content from the API when the editor is ready and the Yjs doc is empty
  useEffect(() => {
    if (editor && initialContent && !contentLoaded) {
      // Wait a tick so Yjs collaboration sync can happen first
      const timer = setTimeout(() => {
        const currentContent = editor.getHTML();
        // Only inject saved content if the editor is still empty (no collab content arrived)
        if (!currentContent || currentContent === "<p></p>") {
          suppressSaveRef.current = true;
          editor.commands.setContent(initialContent);
          // Clear suppression after a short delay so any async Yjs observer fires are also caught
          setTimeout(() => { suppressSaveRef.current = false; }, 50);
        }
        setContentLoaded(true);
      }, 500);
      return () => clearTimeout(timer);
    }
  }, [editor, initialContent, contentLoaded]);

  if (!editor) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="w-8 h-8 border-2 border-otter-600 border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  return (
    <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-gray-200 bg-gray-50 flex-wrap">
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleBold().run()}
          active={editor.isActive("bold")}
          title="Bold"
        >
          <Bold size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleItalic().run()}
          active={editor.isActive("italic")}
          title="Italic"
        >
          <Italic size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleStrike().run()}
          active={editor.isActive("strike")}
          title="Strikethrough"
        >
          <Strikethrough size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleCode().run()}
          active={editor.isActive("code")}
          title="Inline code"
        >
          <Code size={16} />
        </ToolbarButton>

        <div className="w-px h-5 bg-gray-300 mx-1" />

        <ToolbarButton
          onClick={() =>
            editor.chain().focus().toggleHeading({ level: 1 }).run()
          }
          active={editor.isActive("heading", { level: 1 })}
          title="Heading 1"
        >
          <Heading1 size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() =>
            editor.chain().focus().toggleHeading({ level: 2 }).run()
          }
          active={editor.isActive("heading", { level: 2 })}
          title="Heading 2"
        >
          <Heading2 size={16} />
        </ToolbarButton>

        <div className="w-px h-5 bg-gray-300 mx-1" />

        <ToolbarButton
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          active={editor.isActive("bulletList")}
          title="Bullet list"
        >
          <List size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          active={editor.isActive("orderedList")}
          title="Numbered list"
        >
          <ListOrdered size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().toggleBlockquote().run()}
          active={editor.isActive("blockquote")}
          title="Quote"
        >
          <Quote size={16} />
        </ToolbarButton>
        <ToolbarButton
          onClick={() => editor.chain().focus().setHorizontalRule().run()}
          active={false}
          title="Horizontal rule"
        >
          <Minus size={16} />
        </ToolbarButton>

        <div className="flex-1" />

        <SaveStatusIndicator
          connectionStatus={connectionStatus}
          isSynced={isSynced}
          hasLocalChanges={hasLocalChanges}
        />
      </div>

      {/* Editor */}
      <EditorContent editor={editor} />
    </div>
  );
}

function SaveStatusIndicator({
  connectionStatus,
  isSynced,
  hasLocalChanges,
}: {
  connectionStatus: "connected" | "disconnected" | "connecting";
  isSynced: boolean;
  hasLocalChanges: boolean;
}) {
  if (connectionStatus === "disconnected") {
    return (
      <div className="flex items-center gap-1.5">
        <WifiOff size={14} className="text-amber-500" />
        <span className="text-xs text-amber-600 font-medium">Offline</span>
      </div>
    );
  }

  if (connectionStatus === "connecting") {
    return (
      <div className="flex items-center gap-1.5">
        <RefreshCw size={14} className="text-blue-500 animate-spin" />
        <span className="text-xs text-blue-600 font-medium">Reconnecting…</span>
      </div>
    );
  }

  if (hasLocalChanges || !isSynced) {
    return (
      <div className="flex items-center gap-1.5">
        <Loader2 size={14} className="text-gray-400 animate-spin" />
        <span className="text-xs text-gray-500">Saving…</span>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1.5">
      <Check size={14} className="text-green-500" />
      <span className="text-xs text-gray-500">All changes saved</span>
    </div>
  );
}

function ToolbarButton({
  onClick,
  active,
  title,
  children,
}: {
  onClick: () => void;
  active: boolean;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={cn(
        "p-1.5 rounded hover:bg-gray-200 transition",
        active ? "bg-gray-200 text-otter-700" : "text-gray-600"
      )}
    >
      {children}
    </button>
  );
}
