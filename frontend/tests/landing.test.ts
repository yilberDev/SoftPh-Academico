/**
 * The public landing page: its links, its access and its illustrative content. Run with `npm test`.
 */
import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { isLoginPage, protectedRule } from '../src/lib/auth/roles.ts';
import {
  DASHBOARD_KPIS,
  DASHBOARD_MOVEMENTS,
  DASHBOARD_NAV,
  EXAMPLE_ROWS,
  FEATURES,
  FINANCE_POINTS,
  HERO_POINTS,
  ILLUSTRATIVE_NOTE,
  LOGIN_PATH,
  NAV_LINKS,
  PRIVACY_NOTE,
  ROLES,
  SECTIONS,
  STEPS,
  VALUE_POINTS,
  exampleCounts,
  exampleTotals,
  formatExampleAmount,
  obligationsLabel
} from '../src/lib/landing/content.ts';
import { ICONS } from '../src/lib/landing/icons.ts';
import { FOCUS_RING, buttonClasses } from '../src/lib/landing/ui.ts';

const spaces = (text: string) => text.replace(/\s/g, ' ');

describe('landing: navigation and access', () => {
  it('every navigation link points to one of the page sections, in order', () => {
    assert.deepEqual(NAV_LINKS.map((link) => link.href), Object.values(SECTIONS).map((id) => `#${id}`));
    assert.deepEqual(NAV_LINKS.map((link) => link.label), ['Inicio', 'Funcionalidades', 'Cómo funciona', 'Beneficios']);
  });

  it('access goes through the real login page, which is not a protected page; "/" is public', () => {
    assert.equal(LOGIN_PATH, '/login');
    assert.equal(isLoginPage(LOGIN_PATH), true);
    assert.equal(protectedRule('/'), undefined);
  });
});

describe('landing: content', () => {
  it('the six features, three steps, two roles and the hero points of the approved design', () => {
    assert.deepEqual(FEATURES.map((f) => f.title), ['Gestión de residentes', 'Pagos y recibos', 'Estados de cuenta',
      'Reportes financieros', 'Solicitudes y PQRS', 'Notificaciones']);
    assert.deepEqual(STEPS.map((s) => [s.number, s.title]), [['01', 'Organiza la información'],
      ['02', 'Gestiona pagos y solicitudes'], ['03', 'Revisa los resultados']]);
    assert.deepEqual(ROLES.map((r) => [r.title, r.items.length]), [['Para administradores', 4], ['Para residentes', 4]]);
    assert.equal(HERO_POINTS.length, 3);
    assert.equal(VALUE_POINTS.length, 3);
  });

  it('every icon used exists in the set', () => {
    for (const { icon } of [...FEATURES, ...STEPS, ...ROLES, ...VALUE_POINTS]) assert.ok(icon in ICONS, icon);
    for (const icon of DASHBOARD_NAV) assert.ok(icon in ICONS, icon);
  });

  it('each feature says which role area uses it; each step and role has its short note', () => {
    assert.deepEqual(FEATURES.map((f) => f.audience), ['Administración', 'Administración y residentes',
      'Administración y residentes', 'Administración', 'Administración y residentes', 'Residentes']);
    for (const step of STEPS) assert.ok(step.outcome.length > 0, step.title);
    assert.deepEqual(ROLES.map((r) => [r.badge, r.area]), [['Administración', 'Área de administración'],
      ['Residentes', 'Panel del residente']]);
  });

  it('claims nothing SoftPh does not do: no exports, prices, clients, certifications or real payment processing', () => {
    const text = JSON.stringify([HERO_POINTS, VALUE_POINTS, FEATURES, FINANCE_POINTS, STEPS, ROLES, PRIVACY_NOTE,
      DASHBOARD_KPIS, DASHBOARD_MOVEMENTS]).toLowerCase();
    for (const claim of ['excel', 'xls', 'export', 'precio', 'plan ', 'cliente', 'testimonio', 'certific', 'garant',
      'pasarela', 'tarjeta', 'ley 675', 'permanencia', 'soporte', '24/7', 'sla']) {
      assert.ok(!text.includes(claim), claim);
    }
  });
});

describe('landing: illustrative financial table', () => {
  it('the totals come from the rows, so the example never contradicts itself', () => {
    const totals = exampleTotals(EXAMPLE_ROWS);
    assert.deepEqual(totals, { Pagado: 360000, Pendiente: 230000, 'En mora': 180000 });
    assert.equal(totals.Pagado + totals.Pendiente + totals['En mora'], EXAMPLE_ROWS.reduce((sum, row) => sum + row.amount, 0));
    assert.deepEqual(new Set(EXAMPLE_ROWS.map((row) => row.status)), new Set(['Pagado', 'Pendiente', 'En mora']));
  });

  it('the counts by status also come from the rows, and read in Spanish', () => {
    assert.deepEqual(exampleCounts(EXAMPLE_ROWS), { Pagado: 2, Pendiente: 2, 'En mora': 1 });
    assert.equal(obligationsLabel(1), '1 obligación');
    assert.equal(obligationsLabel(5), '5 obligaciones');
    assert.equal(ILLUSTRATIVE_NOTE, 'Vista ilustrativa con datos de ejemplo.');
  });

  it('the dashboard example uses the same statuses as the table', () => {
    assert.deepEqual(new Set(DASHBOARD_MOVEMENTS.map((row) => row.status)), new Set(['Pagado', 'Pendiente', 'En mora']));
  });

  it('amounts in Colombian pesos, without cents', () => {
    assert.equal(spaces(formatExampleAmount(180000)), '$ 180.000');
    assert.equal(spaces(formatExampleAmount(360000)), '$ 360.000');
  });
});

describe('landing: shared button styles', () => {
  it('every button keeps a 44 px touch target and a visible focus ring', () => {
    for (const variant of ['primary', 'secondary', 'ghost'] as const) {
      const classes = buttonClasses(variant);
      assert.ok(classes.includes('min-h-11'), variant);
      assert.ok(classes.includes(FOCUS_RING), variant);
    }
    for (const variant of ['inverse', 'onDark'] as const) {
      const classes = buttonClasses(variant, 'lg');
      assert.ok(classes.includes('min-h-12'), variant);
      assert.ok(classes.includes('focus-visible:outline-white'), variant);
    }
  });
});
