#pragma once
#include <windows.h>
#include <oleidl.h>
#include <shlobj.h>
#include <atomic>
#include <cstring>
#include <string>

namespace background {
// Each GetData transfers a distinct HGLOBAL to the caller's STGMEDIUM.
class FileData final : public IDataObject {
    std::atomic<ULONG> refs_{1};
    std::wstring file_;
public:
    explicit FileData(std::wstring file) : file_(std::move(file)) {}
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IDataObject) return E_NOINTERFACE;
        *out = static_cast<IDataObject*>(this); AddRef(); return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++refs_; }
    ULONG STDMETHODCALLTYPE Release() override {
        auto refs = --refs_; if (!refs) delete this; return refs;
    }
    HRESULT STDMETHODCALLTYPE QueryGetData(FORMATETC* format) override {
        if (!format) return E_POINTER;
        if (format->cfFormat != CF_HDROP || format->dwAspect != DVASPECT_CONTENT ||
            format->lindex != -1 || !(format->tymed & TYMED_HGLOBAL) || format->ptd)
            return DV_E_FORMATETC;
        return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetData(FORMATETC* format, STGMEDIUM* medium) override {
        if (!medium) return E_POINTER;
        *medium = {};
        HRESULT hr = QueryGetData(format); if (FAILED(hr)) return hr;
        size_t bytes = sizeof(DROPFILES) + (file_.size() + 2) * sizeof(wchar_t);
        HGLOBAL memory = GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, bytes);
        if (!memory) return E_OUTOFMEMORY;
        auto data = static_cast<DROPFILES*>(GlobalLock(memory));
        if (!data) { GlobalFree(memory); return E_OUTOFMEMORY; }
        data->pFiles = sizeof(DROPFILES); data->fWide = TRUE;
        std::memcpy(reinterpret_cast<BYTE*>(data) + sizeof(DROPFILES), file_.c_str(),
                    (file_.size() + 1) * sizeof(wchar_t));
        GlobalUnlock(memory);
        medium->tymed = TYMED_HGLOBAL; medium->hGlobal = memory; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetDataHere(FORMATETC*, STGMEDIUM*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetCanonicalFormatEtc(FORMATETC*, FORMATETC* out) override {
        if (out) out->ptd = nullptr; return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE SetData(FORMATETC*, STGMEDIUM*, BOOL) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE EnumFormatEtc(DWORD direction, IEnumFORMATETC** out) override {
        if (!out) return E_POINTER;
        *out = nullptr; if (direction != DATADIR_GET) return E_NOTIMPL;
        FORMATETC format{CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
        return SHCreateStdEnumFmtEtc(1, &format, out);
    }
    HRESULT STDMETHODCALLTYPE DAdvise(FORMATETC*, DWORD, IAdviseSink*, DWORD*) override {
        return OLE_E_ADVISENOTSUPPORTED;
    }
    HRESULT STDMETHODCALLTYPE DUnadvise(DWORD) override { return OLE_E_ADVISENOTSUPPORTED; }
    HRESULT STDMETHODCALLTYPE EnumDAdvise(IEnumSTATDATA** out) override {
        if (out) *out = nullptr; return OLE_E_ADVISENOTSUPPORTED;
    }
};
}
