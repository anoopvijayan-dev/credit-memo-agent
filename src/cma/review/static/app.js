// The review workspace page (C5). It renders what the server sends and computes no money.
// Text from the case is set with textContent, never as markup (R34).
"use strict";

const token = document.querySelector('meta[name="cma-token"]').content;

async function api(path) {
  const reply = await fetch(path, { headers: { "X-CMA-Token": token }, cache: "no-store" });
  if (!reply.ok) {
    throw new Error("the workspace refused the request (" + reply.status + ")");
  }
  return reply.json();
}

async function load() {
  const line = document.getElementById("case");
  try {
    const c = await api("/api/case");
    line.textContent = "Case " + c.case + (c.label ? " — " + c.label : "");
  } catch (e) {
    line.textContent = "Could not load the case: " + e.message;
  }
}

load();
