/* Progressive enhancement: one file per request bounds server memory and lets
 * an upload report partial success. The regular multipart form works without JS.
 * Do not automatically retry an ambiguous network failure: the server may have
 * accepted that file already, and silently retrying would create a duplicate. */
"use strict";

(() => {
  const form = document.querySelector("[data-upload-form]");
  if (!form || !window.FormData || !window.XMLHttpRequest) return;
  const input = form.querySelector('input[type="file"]');
  const area = document.createElement("section");
  area.className = "upload-progress";
  area.hidden = true;
  area.setAttribute("aria-label", "Progression de l’ajout");
  form.after(area);
  let uploading = false;
  window.addEventListener("beforeunload", event => {
    if (uploading) { event.preventDefault(); event.returnValue = ""; }
  });

  function send(data, progress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", form.action || window.location.href);
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", event => {
        if (event.lengthComputable) progress.value = Math.round(event.loaded / event.total * 100);
      });
      xhr.addEventListener("load", () => {
        try {
          const result = JSON.parse(xhr.responseText);
          if (!Array.isArray(result.uploaded) || !Array.isArray(result.errors)) throw new Error();
          resolve(result);
        } catch (_) {
          reject(new Error("Réponse inattendue. Vérifiez votre album avant de réessayer."));
        }
      });
      xhr.addEventListener("error", () => reject(new Error("Connexion interrompue. Vérifiez votre album avant de réessayer.")));
      xhr.send(data);
    });
  }

  form.addEventListener("submit", async event => {
    if (!input.files.length || !form.reportValidity()) return;
    event.preventDefault();
    if (uploading) return;
    const files = Array.from(input.files);
    const initial = new FormData(form);
    let albumId = initial.get("album") || "";
    let successes = 0;
    uploading = true;
    area.replaceChildren();
    area.hidden = false;
    const summary = document.createElement("p");
    summary.setAttribute("role", "status");
    const rows = document.createElement("ul");
    area.append(summary, rows);
    const controls = Array.from(form.elements).filter(control => !control.disabled);
    controls.forEach(control => { control.disabled = true; });
    try {
      for (const [index, file] of files.entries()) {
        summary.textContent = `Ajout de la photo ${index + 1} sur ${files.length}…`;
        const row = document.createElement("li");
        const label = document.createElement("span");
        label.textContent = file.name;
        const progress = document.createElement("progress");
        progress.max = 100;
        progress.value = 0;
        progress.setAttribute("aria-label", file.name);
        const resultLabel = document.createElement("span");
        row.append(label, progress, resultLabel);
        rows.append(row);
        const data = new FormData();
        for (const [name, value] of initial.entries()) {
          if (!["files", "album", "new_album"].includes(name)) data.append(name, value);
        }
        data.set("album", albumId);
        data.set("new_album", albumId ? "" : initial.get("new_album") || "");
        data.set("files", file, file.name);
        let result;
        try { result = await send(data, progress); }
        catch (error) { resultLabel.textContent = error.message; row.classList.add("upload-error"); break; }
        if (result.album_id && /^[0-9a-f-]{36}$/i.test(result.album_id)) albumId = result.album_id;
        if (result.uploaded.length) {
          successes += result.uploaded.length;
          progress.value = 100;
          resultLabel.textContent = "Ajoutée · préparation de l’aperçu";
        } else {
          row.classList.add("upload-error");
          resultLabel.textContent = result.errors.map(error => error.message).join(" ") || "Ajout impossible.";
          // A form-level error (e.g. revoked permission) applies to the entire queue.
          if (result.album_id === null) break;
        }
      }
    } finally {
      uploading = false;
      controls.forEach(control => { control.disabled = false; });
      summary.textContent = `${successes} photo${successes > 1 ? "s" : ""} ajoutée${successes > 1 ? "s" : ""} sur ${files.length}.`;
      input.value = "";
      if (albumId) {
        // Reset to the actual destination, including an album just created by POST.
        const select = form.elements.namedItem("album");
        if (!Array.from(select.options).some(option => option.value === albumId)) {
          select.add(new Option(initial.get("new_album") || "Album créé", albumId));
        }
        select.value = albumId;
        form.elements.namedItem("new_album").value = "";
        if (form.dataset.albumsUrl) {
          const link = document.createElement("a");
          link.className = "button button-primary";
          link.href = `${form.dataset.albumsUrl}${albumId}/`;
          link.textContent = "Ouvrir l’album";
          area.append(link);
        }
      }
    }
  });
})();
