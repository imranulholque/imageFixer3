package main

// Admin-side file manager for the shared folder.
// Lets the desktop window drag & drop / paste files into the shared folder,
// browse it, make folders, delete and open items. Every route here is wrapped
// in local() (this PC only) and mutating routes also check the Origin header.

import (
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// sameOrigin blocks other websites from poking the admin API through the browser.
func sameOrigin(r *http.Request) bool {
	o := r.Header.Get("Origin")
	if o == "" {
		return true
	}
	u, err := url.Parse(o)
	return err == nil && u.Host == r.Host
}

// cleanSub makes a safe relative sub-path: no "..", no drive letters, no bad characters.
func cleanSub(sub string) string {
	sub = strings.ReplaceAll(sub, "\\", "/")
	var parts []string
	for _, p := range strings.Split(sub, "/") {
		p = strings.TrimSpace(p)
		if p == "" || p == "." || p == ".." {
			continue
		}
		parts = append(parts, cleanName(p))
	}
	return strings.Join(parts, "/")
}

// rootDir picks which folder the desktop file manager is working on:
// "recv" = files received from the phone, anything else = the shared folder.
func rootDir(root string) string {
	c := get()
	if root == "recv" {
		return c.RecvDir
	}
	return c.ShareDir
}

// rootResolve maps a path relative to the chosen folder to a real path (never outside it).
func rootResolve(root, sub string) (base, full string, ok bool) {
	d := rootDir(root)
	if d == "" {
		return "", "", false
	}
	base = filepath.Clean(d)
	sub = strings.Trim(filepath.ToSlash(sub), "/")
	full = filepath.Join(base, filepath.FromSlash(sub))
	rel, err := filepath.Rel(base, full)
	if err != nil || rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", "", false
	}
	return base, full, true
}

func adminReadJSON(r *http.Request, v any) bool {
	return json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(v) == nil
}

// GET /admin/files?path=sub&root=share|recv  ->  what is inside the shared folder, or the folder the phone sends to
// (works even if phone sharing is off)
func adminFiles(w http.ResponseWriter, r *http.Request) {
	type item struct {
		Name string `json:"name"`
		Dir  bool   `json:"dir"`
		Size int64  `json:"size"`
		T    int64  `json:"t"`
	}
	out := []item{}
	sub := strings.Trim(filepath.ToSlash(r.URL.Query().Get("path")), "/")
	root := r.URL.Query().Get("root")
	base, full, ok := rootResolve(root, sub)
	if !ok {
		writeJSON(w, map[string]any{"set": rootDir(root) != "", "dir": "", "path": "", "items": out})
		return
	}
	entries, _ := os.ReadDir(full)
	for _, e := range entries {
		if strings.HasPrefix(e.Name(), ".") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		out = append(out, item{e.Name(), e.IsDir(), info.Size(), info.ModTime().Unix()})
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].Dir != out[j].Dir {
			return out[i].Dir
		}
		return out[i].T > out[j].T
	})
	writeJSON(w, map[string]any{"set": true, "dir": base, "path": sub, "items": out})
}

// POST /admin/put?path=sub&name=file.ext   (raw body)  ->  save one dropped / pasted file
func adminPut(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || !sameOrigin(r) {
		http.Error(w, "forbidden", 403)
		return
	}
	q := r.URL.Query()
	_, dir, ok := rootResolve(q.Get("root"), cleanSub(q.Get("path")))
	if !ok {
		http.Error(w, "Choose a folder first", 400)
		return
	}
	if err := os.MkdirAll(dir, 0755); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	f, dest, err := createUnique(dir, cleanName(q.Get("name")))
	if err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	n, err := io.Copy(f, r.Body)
	f.Close()
	if err != nil {
		os.Remove(dest)
		http.Error(w, err.Error(), 500)
		return
	}
	notifyPhones() // a file appeared in a folder the phones may be looking at
	writeJSON(w, map[string]any{"name": filepath.Base(dest), "size": n})
}

// POST /admin/mkdir  {path, name}
func adminMkdir(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || !sameOrigin(r) {
		http.Error(w, "forbidden", 403)
		return
	}
	var b struct{ Path, Name, Root string }
	if !adminReadJSON(r, &b) {
		http.Error(w, "bad request", 400)
		return
	}
	sub := cleanSub(b.Path + "/" + cleanName(b.Name))
	_, full, ok := rootResolve(b.Root, sub)
	if !ok || sub == "" {
		http.Error(w, "Choose a folder first", 400)
		return
	}
	if err := os.MkdirAll(full, 0755); err != nil {
		http.Error(w, err.Error(), 500)
		return
	}
	notifyPhones()
	writeJSON(w, map[string]any{"ok": true})
}

// POST /admin/del  {path}   (a file, or an empty folder)
func adminDel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || !sameOrigin(r) {
		http.Error(w, "forbidden", 403)
		return
	}
	var b struct{ Path, Root string }
	if !adminReadJSON(r, &b) {
		http.Error(w, "bad request", 400)
		return
	}
	sub := cleanSub(b.Path)
	base, full, ok := rootResolve(b.Root, sub)
	if !ok || sub == "" || filepath.Clean(full) == base {
		http.Error(w, "forbidden", 403)
		return
	}
	if err := os.Remove(full); err != nil {
		if st, e := os.Stat(full); e == nil && st.IsDir() {
			http.Error(w, "That folder is not empty", 409)
			return
		}
		http.Error(w, err.Error(), 500)
		return
	}
	notifyPhones()
	writeJSON(w, map[string]any{"ok": true})
}

// POST /admin/open  {path}  ->  open a file or folder (path "" = the shared folder) with the system default
func adminOpen(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost || !sameOrigin(r) {
		http.Error(w, "forbidden", 403)
		return
	}
	var b struct{ Path, Root string }
	if !adminReadJSON(r, &b) {
		http.Error(w, "bad request", 400)
		return
	}
	_, full, ok := rootResolve(b.Root, cleanSub(b.Path))
	if !ok {
		http.Error(w, "Choose a folder first", 400)
		return
	}
	if _, err := os.Stat(full); err != nil {
		if err := os.MkdirAll(full, 0755); err != nil {
			http.Error(w, err.Error(), 500)
			return
		}
	}
	openBrowser(full)
	writeJSON(w, map[string]any{"ok": true})
}

// GET /admin/pv/<path>  ->  inline image/video for the thumbnails in the desktop window
func adminPreview(w http.ResponseWriter, r *http.Request) {
	_, full, ok := rootResolve(r.URL.Query().Get("root"), strings.TrimPrefix(r.URL.Path, "/admin/pv/"))
	if !ok {
		http.NotFound(w, r)
		return
	}
	ct, ok := previewTypes[strings.ToLower(filepath.Ext(full))]
	if !ok || ct == "image/svg+xml" {
		http.Error(w, "no preview", 404)
		return
	}
	f, err := os.Open(full)
	if err != nil {
		http.NotFound(w, r)
		return
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil || st.IsDir() {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", ct)
	w.Header().Set("X-Content-Type-Options", "nosniff")
	http.ServeContent(w, r, "", st.ModTime(), f)
}
