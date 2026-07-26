import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"

import App from "@/App"
import { TooltipProvider } from "@/components/ui/tooltip"
import "@/index.css"

const savedTheme = window.localStorage.getItem("tide.web.theme")
const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches
document.documentElement.classList.toggle(
  "dark",
  savedTheme === "dark" || (!savedTheme && prefersDark),
)

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TooltipProvider delayDuration={350}>
        <App />
      </TooltipProvider>
    </QueryClientProvider>
  </StrictMode>,
)
