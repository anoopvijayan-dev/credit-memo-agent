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

// "On this page": a link to each section with a heading, built from the page as served, so
// it lists exactly what the server rendered. Section ids come from the server; a section
// without one is given a plain numbered id.
function contents() {
  const toc = document.getElementById("toc");
  const main = document.getElementById("workspace");
  if (!toc || !main) {
    return;
  }
  const heads = main.querySelectorAll("section > h2");
  if (heads.length < 2) {
    toc.hidden = true;
    document.body.classList.add("no-toc");
    return;
  }
  const label = document.createElement("p");
  label.className = "toc-label";
  label.textContent = "On this page";
  const list = document.createElement("nav");
  list.setAttribute("aria-label", "On this page");
  heads.forEach((h, i) => {
    const section = h.parentElement;
    if (!section.id) {
      section.id = "part-" + (i + 1);
    }
    const a = document.createElement("a");
    a.href = "#" + section.id;
    a.textContent = h.firstChild ? h.firstChild.textContent.trim() : h.textContent.trim();
    list.appendChild(a);
  });
  toc.appendChild(label);
  toc.appendChild(list);
  const links = Array.from(list.querySelectorAll("a"));
  if ("IntersectionObserver" in window) {
    const seen = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          links.forEach((a) => a.classList.toggle("here", a.getAttribute("href") === "#" + entry.target.id));
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px" });
    heads.forEach((h) => seen.observe(h.parentElement));
  }
}

load();
contents();
