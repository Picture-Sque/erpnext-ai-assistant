import path from "path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
	plugins: [react()],
	base: "/assets/ai_assistant/desk/",
	build: {
		outDir: path.resolve(__dirname, "../ai_assistant/public/desk"),
		emptyOutDir: true,
		cssCodeSplit: true,
		rollupOptions: {
			output: {
				entryFileNames: "assistant.bundle.js",
				chunkFileNames: "chunks/[name].js",
				assetFileNames: "assistant.bundle.[ext]"
			}
		}
	}
});