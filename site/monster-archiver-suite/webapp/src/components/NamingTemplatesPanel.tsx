import React, { useState, useEffect } from "react";
import { FolderCog, Save, RefreshCw, CheckCircle2, AlertTriangle, Sparkles } from "lucide-react";

const TOKENS = ["artist", "albumartist", "album", "title", "year", "track", "genre", "isrc", "disc", "composer"];

const DEFAULT_FOLDER_TEMPLATE = "{artist}/{year} - {album}";
const DEFAULT_FILENAME_TEMPLATE = "{track} - {title}";

interface NamingValues {
  NAMING_FOLDER_TEMPLATE: string;
  NAMING_FILENAME_TEMPLATE: string;
  PRIMARY_ARTIST_BY_FAME: boolean;
}

export default function NamingTemplatesPanel() {
  const [values, setValues] = useState<NamingValues>({
    NAMING_FOLDER_TEMPLATE: DEFAULT_FOLDER_TEMPLATE,
    NAMING_FILENAME_TEMPLATE: DEFAULT_FILENAME_TEMPLATE,
    PRIMARY_ARTIST_BY_FAME: true
  });
  const [initialValues, setInitialValues] = useState<NamingValues>(values);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      const next: NamingValues = {
        NAMING_FOLDER_TEMPLATE: data.values.NAMING_FOLDER_TEMPLATE || DEFAULT_FOLDER_TEMPLATE,
        NAMING_FILENAME_TEMPLATE: data.values.NAMING_FILENAME_TEMPLATE || DEFAULT_FILENAME_TEMPLATE,
        PRIMARY_ARTIST_BY_FAME: data.values.PRIMARY_ARTIST_BY_FAME ?? true
      };
      setValues(next);
      setInitialValues(next);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const dirty = JSON.stringify(values) !== JSON.stringify(initialValues);

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setInitialValues(values);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  const inputClass =
    "w-full bg-void-950 border border-void-700 rounded-lg py-2.5 px-4 text-white text-sm font-mono focus:outline-none focus:border-deezer-500 transition-colors";

  return (
    <div className="bg-void-900 rounded-xl border border-void-700 p-6">
      <h3 className="text-white font-medium text-lg mb-5 flex items-center gap-2 border-b border-void-700/60 pb-3">
        <FolderCog className="w-5 h-5 text-deezer-400" />
        Naming Templates
      </h3>

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <RefreshCw className="w-6 h-6 text-deezer-400 animate-spin" />
        </div>
      ) : (
        <>
          <div className="space-y-4">
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                Folder Template
              </label>
              <input
                type="text"
                value={values.NAMING_FOLDER_TEMPLATE}
                onChange={(e) => setValues((v) => ({ ...v, NAMING_FOLDER_TEMPLATE: e.target.value }))}
                placeholder={DEFAULT_FOLDER_TEMPLATE}
                className={inputClass}
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider block mb-1.5">
                Filename Template
              </label>
              <input
                type="text"
                value={values.NAMING_FILENAME_TEMPLATE}
                onChange={(e) => setValues((v) => ({ ...v, NAMING_FILENAME_TEMPLATE: e.target.value }))}
                placeholder={DEFAULT_FILENAME_TEMPLATE}
                className={inputClass}
              />
            </div>

            <p className="text-slate-600 text-[11px] leading-relaxed">
              Tokens: {TOKENS.map((t) => (
                <code key={t} className="text-deezer-400/80 mr-1.5">
                  {`{${t}}`}
                </code>
              ))}
            </p>

            <div className="flex items-center justify-between gap-4 bg-void-950 rounded-lg border border-void-700/60 px-4 py-3">
              <div className="flex items-start gap-2.5">
                <Sparkles className="w-4 h-4 text-deezer-400 mt-0.5 shrink-0" />
                <div>
                  <p className="text-slate-300 text-xs font-medium">Pick multi-artist folder by fame</p>
                  <p className="text-slate-600 text-[11px] mt-0.5 leading-relaxed max-w-md">
                    A track credited to more than one artist files under whichever one has more Deezer fans, so
                    "{"{artist}"}" resolves to one clean folder instead of a full feature-credit string. Every
                    credited artist is still written to the track's tags, so players show everyone during playback.
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setValues((v) => ({ ...v, PRIMARY_ARTIST_BY_FAME: !v.PRIMARY_ARTIST_BY_FAME }))}
                className={`relative w-11 h-6 rounded-full transition-colors cursor-pointer border-0 shrink-0 ${
                  values.PRIMARY_ARTIST_BY_FAME ? "bg-deezer-600" : "bg-slate-800"
                }`}
              >
                <span
                  className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
                    values.PRIMARY_ARTIST_BY_FAME ? "translate-x-5" : "translate-x-0"
                  }`}
                />
              </button>
            </div>
          </div>

          {error && (
            <p className="text-rose-400 text-xs mt-4 flex items-center gap-1.5">
              <AlertTriangle className="w-3.5 h-3.5" /> {error}
            </p>
          )}

          <div className="flex items-center justify-between gap-3 mt-5">
            <p className="text-slate-600 text-[11px]">
              Only affects files archived from now on — your existing library folders are untouched.
            </p>
            <button
              onClick={handleSave}
              disabled={saving || !dirty}
              className="px-5 py-2.5 bg-gradient-to-r from-deezer-600 to-flow-500 hover:from-deezer-500 hover:to-flow-400 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center gap-2 cursor-pointer border-0 shrink-0"
            >
              {saving ? (
                <>
                  <RefreshCw className="w-4 h-4 animate-spin" /> Saving...
                </>
              ) : saved ? (
                <>
                  <CheckCircle2 className="w-4 h-4" /> Saved
                </>
              ) : (
                <>
                  <Save className="w-4 h-4" /> Save Templates
                </>
              )}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
