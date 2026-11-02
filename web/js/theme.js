// Apply before painting; storage can be unavailable in restricted browser contexts.
(() => {
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = "system";
  try { preference = localStorage.getItem("yap_theme") || "system"; } catch {}
  if (!["system", "light", "dark"].includes(preference)) preference = "system";
  const apply = () => {
    document.documentElement.dataset.theme = preference === "system"
      ? (system.matches ? "dark" : "light") : preference;
  };
  apply();
  system.addEventListener("change", apply);
  document.addEventListener("DOMContentLoaded", () => {
    const select = document.getElementById("theme-select");
    select.value = preference;
    select.addEventListener("change", () => {
      preference = select.value;
      try { localStorage.setItem("yap_theme", preference); } catch {}
      apply();
    });
  });
})();
