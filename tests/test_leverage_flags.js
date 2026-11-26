#!/usr/bin/env node
/**
 * Load isLev / isModestLev from index.html and check them against data.js.
 * Does not modify data.js.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const ROOT = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(ROOT, 'index.html'), 'utf8');
const dataSrc = fs.readFileSync(path.join(ROOT, 'data.js'), 'utf8');

function extract(name, src) {
  const start = src.indexOf(name);
  if (start < 0) throw new Error('missing ' + name);
  return start;
}

const start = html.indexOf('const hasMult=');
const end = html.indexOf('const fmt=', start);
if (start < 0 || end < 0) throw new Error('could not extract leverage helpers from index.html');
const helpers = html.slice(start, end);

const ctx = {};
vm.createContext(ctx);
vm.runInContext(dataSrc + '\nthis.D=D;\n' + helpers + `
this.hasMult=hasMult;
this.D1806B3=D1806B3;
this.isDailyLevName=isDailyLevName;
this.isAltAc=isAltAc;
this.isLev=isLev;
this.isModestLev=isModestLev;
this.levBadge=levBadge;
`, ctx);

const { D, hasMult, D1806B3, isLev, isModestLev, levBadge } = ctx;
const MULT = /\b(1\.25|1\.5|1\.75|2|3)x/i;
const LOAN = /leveraged loan/i;
const oldIsLev = e => e.fund_type === 'Leveraged' || (MULT.test(e.name) && !LOAN.test(e.name));

const bySym = Object.fromEntries(D.map(e => [e.symbol, e]));
function must(sym) {
  const e = bySym[sym];
  assert(e, 'missing symbol ' + sym);
  return e;
}

const errors = [];
function check(cond, msg) {
  if (!cond) errors.push(msg);
}

// MMDC:CA is the motivating case
const mmdc = must('MMDC:CA');
check(isModestLev(mmdc) && !isLev(mmdc), 'MMDC:CA should be modest, not isLev');
check(/&#9888;/.test(levBadge(mmdc)), 'MMDC:CA badge should show the leverage symbol');
check(!/Leveraged ETF/.test(levBadge(mmdc)), 'MMDC:CA must not show the short-term leveraged warning');

// True daily products that lack a 2x/3x token in the name must keep the warning.
// Skip tickers absent from the current ingest (UBR dropped out of the 2026-11-25 export).
for (const sym of ['UBR', 'XPP', 'PLTA', 'NVDB', 'CRCA', 'COIA', 'MST', 'TDAX', 'TSYX']) {
  const e = bySym[sym];
  if (!e) continue;
  check(isLev(e) && !isModestLev(e), `${sym} (${e.name}) must keep the strong leveraged warning`);
}

// d1806b3's 13 Inverse/ETN targets
for (const sym of D1806B3) {
  const e = must(sym);
  check(isLev(e) && !isModestLev(e), `${sym} (${e.name}) is a d1806b3 target and must stay isLev`);
}

// Every name with a 2x/3x/1.25-1.75x multiplier (except leveraged-loan) keeps the warning
const multiplied = D.filter(e => MULT.test(e.name) && !LOAN.test(e.name));
for (const e of multiplied) {
  check(isLev(e) && !isModestLev(e), `${e.symbol} (${e.name}) has a multiplier and must stay isLev`);
}

// 1x inverse and leveraged-loan funds stay unflagged
for (const sym of ['SPDN', 'AAPD', 'PALD', 'AMDD', 'MUD', 'LVLN']) {
  const e = must(sym);
  check(!isLev(e) && !isModestLev(e), `${sym} (${e.name}) must remain unflagged`);
}

// Do not newly flag ultra-short bonds, ultra dividend, or option-income "Short" funds
for (const sym of ['RDIV', 'ULTY', 'FUSI', 'JPST', 'BITI', 'FIAT', 'CRSH']) {
  const e = must(sym);
  check(!isLev(e), `${sym} (${e.name}) must not gain the leveraged warning`);
}

// A fund cannot be both
for (const e of D) {
  check(!(isLev(e) && isModestLev(e)), `${e.symbol} is both isLev and isModestLev`);
}

// Filter membership: modest funds are excluded from "Leveraged only"
const moved = D.filter(e => oldIsLev(e) && isModestLev(e) && !isLev(e)).map(e => e.symbol).sort();
const lostWarning = D.filter(e => oldIsLev(e) && !isLev(e) && !isModestLev(e));
check(lostWarning.length === 0, 'funds lost the warning without moving to modest: ' + lostWarning.map(e => e.symbol).join(', '));

const newlyFlagged = D.filter(e => isLev(e) && !oldIsLev(e));
check(newlyFlagged.length === 0, 'unexpected new isLev flags: ' + newlyFlagged.map(e => e.symbol + ' ' + e.name).join('; '));

check(moved.includes('MMDC:CA'), 'MMDC:CA should be in the moved list');
check(!moved.includes('UBR') && !moved.includes('MST') && !moved.includes('TDAX') && !moved.includes('TSYX'), 'true daily products must not move to modest');

if (errors.length) {
  console.error('FAIL (%d)\n' + errors.join('\n'), errors.length);
  process.exit(1);
}

console.log('OK');
console.log('funds', D.length);
console.log('old isLev', D.filter(oldIsLev).length);
console.log('new isLev', D.filter(isLev).length);
console.log('modest', D.filter(isModestLev).length);
console.log('moved from leveraged warning to modest:', moved.length);
console.log(moved.join('\n'));
