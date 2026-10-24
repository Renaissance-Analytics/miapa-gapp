import { createRoot } from "react-dom/client";
import { initTheme } from "@particle-academy/react-fancy";
import App from "./App.js";
import "./styles.css";

initTheme({ storageKey: "maipa.theme.signed-out" });
const root = document.getElementById("root");
if (!root) throw new Error("Missing application root");
createRoot(root).render(<App />);
