import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { X } from "lucide-react";

import { cn } from "@/lib/utils";

/** A thin, dependency-free toast. `sonner` would be a dependency for four lines of
 *  rendering, and the harness's rule about not pulling in a library for something it
 *  can read in a file applies to the client too. */

interface Toast {
  id: number;
  title: string;
  detail?: string;
  variant: "info" | "error" | "pass";
}

interface ToastApi {
  push: (toast: Omit<Toast, "id">) => void;
}

const ToastContext = createContext<ToastApi | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const push = useCallback((toast: Omit<Toast, "id">) => {
    setToasts((current) => [...current, { ...toast, id: Date.now() + Math.random() }]);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((current) => current.filter((item) => item.id !== id));
  }, []);

  const api = useMemo(() => ({ push }), [push]);

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="pointer-events-none fixed bottom-9 right-3 z-[60] flex w-80 flex-col gap-1.5">
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onDismiss={() => dismiss(toast.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  useEffect(() => {
    // An error stays until it is dismissed: a toast that removes itself is how a
    // refusal gets missed while the reviewer is reading the step it was about.
    if (toast.variant === "error") return;
    const timer = window.setTimeout(onDismiss, 4_000);
    return () => window.clearTimeout(timer);
  }, [toast, onDismiss]);

  return (
    <div
      role={toast.variant === "error" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto flex items-start gap-2 rounded-md border bg-popover px-2.5 py-2 text-xs shadow-lg",
        toast.variant === "error" && "border-status-fail/45",
        toast.variant === "pass" && "border-status-pass/45",
        toast.variant === "info" && "border-border",
      )}
    >
      <div className="min-w-0 flex-1">
        <div className="font-medium">{toast.title}</div>
        {toast.detail && (
          <div className="mt-0.5 break-words text-[11px] text-muted-foreground">
            {toast.detail}
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="rounded text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/60"
      >
        <X className="size-3" />
        <span className="sr-only">Fechar</span>
      </button>
    </div>
  );
}

export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) throw new Error("useToast requires a ToastProvider");
  return api;
}
