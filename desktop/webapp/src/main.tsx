import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "@/index.css";
import { App } from "@/App";
import { TipLayer } from "@/components/TipLayer";
import { ToastProvider } from "@/components/ui/toast";
import { TooltipProvider } from "@/components/ui/tooltip";

const root = document.getElementById("root");
if (!root) throw new Error("#root is missing from index.html");

createRoot(root).render(
  <StrictMode>
    <TooltipProvider>
      <ToastProvider>
        <App />
        {/* One tooltip for the whole window, driven off `[data-tip]`. See TipLayer
            for why the collection does not mount one per row. */}
        <TipLayer />
      </ToastProvider>
    </TooltipProvider>
  </StrictMode>,
);
