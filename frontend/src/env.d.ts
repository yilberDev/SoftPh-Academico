/// <reference types="astro/client" />

declare namespace App {
  interface Locals {
    /** Session validated by the backend for protected pages (set by the middleware). */
    auth?: import('./lib/api/auth').SessionInfo;
  }
}
