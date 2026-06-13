import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "@/popup/App";
import "@/styles.css";

const rootElement = document.getElementById("root");

if (!rootElement) {
  throw new Error("Popup root element was not found.");
}

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>
);
