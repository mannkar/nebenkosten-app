/*
 * Generisches Sortieren + Spalten-Filtern fuer <table class="jstable">.
 * Ohne Build-Schritt, reines Vanilla-JS - wird einmal ueber base.html auf
 * allen Seiten geladen und wirkt automatisch auf jede Tabelle mit der
 * Klasse "jstable".
 *
 * Erwartungen an die Tabelle:
 * - Die erste <tr> ist die Kopfzeile (kein <thead> noetig).
 * - Spalten, die weder sortiert noch gefiltert werden sollen (z.B. eine
 *   Aktionen-Spalte mit Buttons), bekommen am <th> data-nosort bzw.
 *   data-nofilter.
 * - Enthaelt eine Zelle statt reinem Text ein Formularfeld (Input/Select),
 *   wird fuer Sortierung/Filterung nicht der Zellentext, sondern das
 *   Attribut data-value der <td> verwendet, falls vorhanden.
 * - Eine vom Server gerenderte "Noch keine Eintraege"-Zeile (Zellenzahl
 *   weicht von der Spaltenzahl ab, i.d.R. per colspan) wird automatisch
 *   erkannt und bleibt unangetastet.
 */
(function () {
  "use strict";

  function zuZahl(text) {
    if (text == null) return NaN;
    var s = String(text).trim();
    if (!s) return NaN;
    var bereinigt = s.replace(/[€%]/g, "").trim();
    if (/^-?\d{1,3}(\.\d{3})*(,\d+)?$/.test(bereinigt)) {
      bereinigt = bereinigt.replace(/\./g, "").replace(",", ".");
    } else if (/^-?\d+([.,]\d+)?$/.test(bereinigt)) {
      bereinigt = bereinigt.replace(",", ".");
    } else {
      return NaN;
    }
    return parseFloat(bereinigt);
  }

  function zellwert(zelle) {
    if (!zelle) return "";
    if (zelle.dataset && zelle.dataset.value !== undefined) return zelle.dataset.value;
    return (zelle.textContent || "").trim();
  }

  function initTabelle(table) {
    var zeilen = Array.prototype.slice.call(table.rows);
    if (!zeilen.length) return;
    var kopfzeile = zeilen[0];
    var kopfzellen = Array.prototype.slice.call(kopfzeile.cells);
    var spaltenzahl = kopfzellen.length;

    var filterzeile = document.createElement("tr");
    filterzeile.className = "jstable-filter-row";
    var filterFelder = [];
    kopfzellen.forEach(function (th, i) {
      var td = document.createElement("td");
      if (!th.hasAttribute("data-nofilter")) {
        var input = document.createElement("input");
        input.type = "text";
        input.placeholder = "Filter…";
        var beschriftung = (th.textContent || "").trim();
        if (beschriftung) input.setAttribute("aria-label", "Filter: " + beschriftung);
        input.addEventListener("input", filternAnwenden);
        td.appendChild(input);
        filterFelder[i] = input;
      }
      filterzeile.appendChild(td);
    });
    kopfzeile.parentNode.insertBefore(filterzeile, kopfzeile.nextSibling);

    var sortierung = { index: -1, richtung: 1 };
    kopfzellen.forEach(function (th, i) {
      if (th.hasAttribute("data-nosort")) return;
      th.classList.add("jstable-sortable");
      th.addEventListener("click", function () {
        if (sortierung.index === i) {
          sortierung.richtung *= -1;
        } else {
          sortierung.index = i;
          sortierung.richtung = 1;
        }
        kopfzellen.forEach(function (t) {
          t.classList.remove("jstable-asc", "jstable-desc");
        });
        th.classList.add(sortierung.richtung === 1 ? "jstable-asc" : "jstable-desc");
        sortieren();
      });
    });

    function datenzeilen() {
      return Array.prototype.slice.call(table.rows).filter(function (r) {
        return (
          r !== kopfzeile &&
          r !== filterzeile &&
          !r.classList.contains("jstable-keine-treffer") &&
          r.cells.length === spaltenzahl
        );
      });
    }

    function sortieren() {
      if (sortierung.index === -1) return;
      var idx = sortierung.index;
      var richtung = sortierung.richtung;
      var zeilenListe = datenzeilen();
      var alleNumerisch = zeilenListe.every(function (r) {
        var w = zellwert(r.cells[idx]);
        return w === "" || !isNaN(zuZahl(w));
      });
      zeilenListe.sort(function (a, b) {
        var wa = zellwert(a.cells[idx]);
        var wb = zellwert(b.cells[idx]);
        var cmp;
        if (alleNumerisch) {
          var na = zuZahl(wa);
          var nb = zuZahl(wb);
          if (isNaN(na)) na = -Infinity;
          if (isNaN(nb)) nb = -Infinity;
          cmp = na - nb;
        } else {
          cmp = wa.localeCompare(wb, "de", { sensitivity: "base", numeric: true });
        }
        return cmp * richtung;
      });
      var elternElement = filterzeile.parentNode;
      zeilenListe.forEach(function (r) {
        elternElement.appendChild(r);
      });
    }

    function filternAnwenden() {
      var zeilenListe = datenzeilen();
      var sichtbare = 0;
      zeilenListe.forEach(function (r) {
        var treffer = true;
        filterFelder.forEach(function (input, i) {
          if (!input || !input.value.trim()) return;
          var begriff = input.value.trim().toLowerCase();
          var wert = zellwert(r.cells[i]).toLowerCase();
          if (wert.indexOf(begriff) === -1) treffer = false;
        });
        r.style.display = treffer ? "" : "none";
        if (treffer) sichtbare++;
      });

      var vorhandeneLeerzeile = table.querySelector(".jstable-keine-treffer");
      var serverseitigeLeerzeile = Array.prototype.slice
        .call(table.rows)
        .some(function (r) {
          return (
            r !== kopfzeile && r !== filterzeile && r.cells.length !== spaltenzahl
          );
        });

      if (sichtbare === 0 && zeilenListe.length > 0 && !serverseitigeLeerzeile) {
        if (!vorhandeneLeerzeile) {
          var neueZeile = document.createElement("tr");
          neueZeile.className = "jstable-keine-treffer";
          var td = document.createElement("td");
          td.colSpan = spaltenzahl;
          td.className = "muted";
          td.textContent = "Keine Treffer für die aktuellen Filter.";
          neueZeile.appendChild(td);
          filterzeile.parentNode.appendChild(neueZeile);
        }
      } else if (vorhandeneLeerzeile) {
        vorhandeneLeerzeile.remove();
      }
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("table.jstable"), initTabelle);
  });
})();
