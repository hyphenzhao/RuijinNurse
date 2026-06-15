#!/usr/bin/env node
/**
 * Patch Capacitor's WebViewAssetHandler to include charset=utf-8
 * in Content-Type headers for text/* MIME types.
 *
 * This fixes Chinese (and other non-Latin) text rendering in WKWebView
 * when using Capacitor without a remote server.url.
 *
 * The issue: Capacitor's custom URL scheme handler returns
 * `Content-Type: text/html` without `charset=utf-8`, causing
 * WKWebView to default to Latin-1 encoding.
 *
 * See: WebViewAssetHandler.swift in @capacitor/ios
 */
const fs = require('fs');
const path = require('path');

const target = path.join(
  __dirname,
  '..',
  'node_modules',
  '@capacitor',
  'ios',
  'Capacitor',
  'Capacitor',
  'WebViewAssetHandler.swift'
);

if (!fs.existsSync(target)) {
  console.log('[patch] WebViewAssetHandler.swift not found — skipping');
  process.exit(0);
}

let content = fs.readFileSync(target, 'utf8');
let modified = false;

// Patch 1: Add charset to Content-Type header for text/* and JavaScript MIME types
const headerPatch = `            var data = Data()
            let mimeType = mimeTypeForExtension(pathExtension: url.pathExtension)
            let mimeTypeWithCharset: String = {
                if mimeType.hasPrefix("text/") || mimeType == "application/javascript" || mimeType == "application/x-javascript" {
                    return mimeType + "; charset=utf-8"
                }
                return mimeType
            }()
            var headers =  [
                "Content-Type": mimeTypeWithCharset,
                "Cache-Control": "no-cache"
            ]`;

// Old pattern to replace
const oldHeaderPattern = /^(\s+)var data = Data\(\)[\s\S]*?var headers =  \[[\s\S]*?\]/m;

if (content.includes('mimeTypeWithCharset')) {
  console.log('[patch] Already patched (header) — skipping');
} else if (oldHeaderPattern.test(content)) {
  content = content.replace(oldHeaderPattern, headerPatch);
  modified = true;
  console.log('[patch] ✓ Patched Content-Type header to include charset=utf-8');
}

// Patch 2: Set textEncodingName to "utf-8" in URLResponse
const oldEncoding = 'textEncodingName: nil)';
const newEncoding = 'textEncodingName: "utf-8")';
if (content.includes(oldEncoding) && !content.includes('textEncodingName: "utf-8"')) {
  content = content.replace(new RegExp(oldEncoding.replace(/[()]/g, '\\$&'), 'g'), newEncoding);
  modified = true;
  console.log('[patch] ✓ Patched textEncodingName from nil to "utf-8"');
}

if (modified) {
  fs.writeFileSync(target, content, 'utf8');
  console.log('[patch] ✅ Capacitor charset patch applied');
} else {
  console.log('[patch] No changes needed');
}
