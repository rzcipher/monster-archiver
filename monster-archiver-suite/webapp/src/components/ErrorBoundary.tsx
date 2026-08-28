import React from "react";

interface ErrorBoundaryState {
  error: Error | null;
}

// Without this, any uncaught render-time exception anywhere in the tree (a
// malformed API response, a null field, etc.) unmounts the whole app and
// leaves a blank white page with no indication of what happened.
export default class ErrorBoundary extends React.Component<{ children: React.ReactNode }, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("Unhandled error in Monster Archiver Suite:", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="min-h-screen bg-void-950 text-slate-300 flex items-center justify-center p-8">
          <div className="max-w-lg w-full bg-void-900 border border-rose-900/40 rounded-xl p-8 text-center shadow-2xl">
            <h1 className="text-white font-display font-semibold text-lg mb-2">Something went wrong</h1>
            <p className="text-slate-400 text-sm mb-1">
              The app hit an unexpected error and couldn't continue rendering.
            </p>
            <p className="text-rose-400 text-xs font-mono mb-6 break-words">{this.state.error.message}</p>
            <button
              onClick={() => window.location.reload()}
              className="px-5 py-2.5 bg-gradient-to-r from-deezer-600 to-flow-500 hover:from-deezer-500 hover:to-flow-400 text-white rounded-lg text-sm font-semibold transition-all shadow-lg shadow-deezer-500/10 cursor-pointer border-0"
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}
