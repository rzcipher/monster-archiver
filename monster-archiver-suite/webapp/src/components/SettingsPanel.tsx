import React, { useState, useEffect } from "react";
import { SlidersHorizontal, RefreshCw, Save, AlertTriangle, CheckCircle2, Eye, EyeOff } from "lucide-react";
import { SETTINGS_SCHEMA, SETTINGS_SECTIONS, SettingField } from "../../lib/settingsSchema";

function FieldInput({
  field,
  value,
  onChange
}: {
  field: SettingField;
  value: any;
  onChange: (val: any) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const baseClass =
    "w-full bg-[#0b0e14]/60 border border-slate-800 rounded-lg py-2.5 px-4 text-white text-sm focus:outline-none focus:border-indigo-500 transition-colors";

  if (field.type === "boolean") {
    return (
      <button
        type="button"
        onClick={() => onChange(!value)}
        className={`relative w-11 h-6 rounded-full transition-colors cursor-pointer border-0 shrink-0 ${
          value ? "bg-indigo-600" : "bg-slate-800"
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-5 h-5 rounded-full bg-white transition-transform ${
            value ? "translate-x-5" : "translate-x-0"
          }`}
        />
      </button>
    );
  }

  if (field.type === "select") {
    return (
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={baseClass}>
        {field.options?.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    );
  }

  if (field.type === "number") {
    return (
      <input
        type="number"
        value={value ?? 0}
        min={field.min}
        max={field.max}
        step={field.step ?? 1}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        className={baseClass}
      />
    );
  }

  if (field.type === "password") {
    return (
      <div className="relative">
        <input
          type={revealed ? "text" : "password"}
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          className={`${baseClass} pr-10 font-mono`}
        />
        <button
          type="button"
          onClick={() => setRevealed((r) => !r)}
          className="absolute inset-y-0 right-0 flex items-center pr-3 text-slate-500 hover:text-slate-300 bg-transparent border-0 cursor-pointer"
        >
          {revealed ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    );
  }

  return <input type="text" value={value ?? ""} onChange={(e) => onChange(e.target.value)} className={baseClass} />;
}

export default function SettingsPanel() {
  const [values, setValues] = useState<Record<string, any>>({});
  const [initialValues, setInitialValues] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/settings");
      const data = await res.json();
      if (!res.ok) throw new Error(data?.error || `HTTP ${res.status}`);
      setValues(data.values);
      setInitialValues(data.values);
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
      setValues(data.values);
      setInitialValues(data.values);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <RefreshCw className="w-8 h-8 text-indigo-400 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {SETTINGS_SECTIONS.map((section) => (
        <div key={section} className="bg-[#0f1117] rounded-xl border border-slate-800 p-6 shadow-2xl ring-1 ring-white/5">
          <h3 className="text-white font-medium text-lg mb-5 flex items-center gap-2 border-b border-slate-800/60 pb-3">
            <SlidersHorizontal className="w-5 h-5 text-indigo-400" />
            {section}
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {SETTINGS_SCHEMA.filter((f) => f.section === section).map((field) => (
              <div key={field.key} className={field.type === "boolean" ? "flex items-center justify-between gap-4" : ""}>
                <div className={field.type === "boolean" ? "" : "mb-1.5"}>
                  <label className="text-xs font-semibold text-slate-400 uppercase tracking-wider block">
                    {field.label}
                  </label>
                  <p className="text-slate-600 text-[11px] mt-0.5 leading-relaxed max-w-md">{field.help}</p>
                </div>
                <FieldInput
                  field={field}
                  value={values[field.key]}
                  onChange={(val) => setValues((prev) => ({ ...prev, [field.key]: val }))}
                />
              </div>
            ))}
          </div>
        </div>
      ))}

      {error && (
        <p className="text-rose-400 text-xs flex items-center gap-1.5">
          <AlertTriangle className="w-3.5 h-3.5" /> {error}
        </p>
      )}

      <div className="sticky bottom-6 flex justify-end">
        <button
          onClick={handleSave}
          disabled={saving || !dirty}
          className="px-6 py-3 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 disabled:from-slate-800 disabled:to-slate-800 disabled:text-slate-600 text-white rounded-lg text-sm font-semibold transition-all shadow-lg flex items-center gap-2 cursor-pointer border-0"
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
              <Save className="w-4 h-4" /> {dirty ? "Save Changes" : "No Changes"}
            </>
          )}
        </button>
      </div>
    </div>
  );
}
