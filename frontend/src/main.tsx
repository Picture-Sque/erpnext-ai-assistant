import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles/global.css";

const ROOT_ID = "root";
const DESK_ROOT_ID = "ai_assistant-root";

const getMountNode = () => {
	// Check for the dedicated Frappe Desk mount point first.
	// Falling back to #root only for standalone dev (index.html).
	// This prevents accidentally mounting inside another framework's root element.
	const existingRoot = document.getElementById(DESK_ROOT_ID) ?? document.getElementById(ROOT_ID);

	if (existingRoot) {
		return existingRoot;
	}

	const createdRoot = document.createElement("div");
	createdRoot.id = DESK_ROOT_ID;
	document.body.appendChild(createdRoot);

	return createdRoot;
};

const mountApp = () => {
	const mountNode = getMountNode();
	const host = mountNode as HTMLElement & { __aiAssistantMounted?: boolean };

	if (host.__aiAssistantMounted) return;
	host.__aiAssistantMounted = true;

	ReactDOM.createRoot(mountNode).render(
		<React.StrictMode>
			<App />
		</React.StrictMode>
	);
};

if (document.readyState === "loading") {
	document.addEventListener("DOMContentLoaded", mountApp, { once: true });
} else {
	mountApp();
}