import type { FilterOptionsResponse } from '@/types/industrial';

interface HeaderProps {
  searchDraft: string;
  selectedState: string;
  selectedDistrict: string;
  selectedIndustryType: string;
  selectedStatus: string;
  options: FilterOptionsResponse | null;
  /** Cascaded district list — only districts valid for the selected state. Falls back to options.districts. */
  availableDistricts?: string[];
  /** Whether a search/filter fetch is in progress */
  isLoading?: boolean;
  onSearchDraftChange: (value: string) => void;
  onStateChange: (value: string) => void;
  onDistrictChange: (value: string) => void;
  onIndustryTypeChange: (value: string) => void;
  onStatusChange: (value: string) => void;
  onSearch: () => void;
  onReset: () => void;
}

const selectStyle: React.CSSProperties = {
  flex: '1 1 140px',
  minWidth: 120,
  padding: '8px 12px',
  borderRadius: 10,
  border: '1px solid #e2e8f0',
  background: '#f8fafc',
  fontSize: 13,
  color: '#374151',
  outline: 'none',
  cursor: 'pointer',
  transition: 'border-color 0.15s, background 0.15s',
  fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
  appearance: 'auto',
};

export function Header({
  searchDraft,
  selectedState,
  selectedDistrict,
  selectedIndustryType,
  selectedStatus,
  options,
  availableDistricts,
  isLoading = false,
  onSearchDraftChange,
  onStateChange,
  onDistrictChange,
  onIndustryTypeChange,
  onStatusChange,
  onSearch,
  onReset,
}: HeaderProps) {
  const districts = availableDistricts ?? options?.districts ?? [];

  return (
    <header
      style={{
        borderRadius: 20,
        border: '1px solid rgba(186,230,255,0.5)',
        background: 'rgba(255,255,255,0.92)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        padding: '14px 18px',
        boxShadow: '0 4px 24px rgba(14,165,233,0.10), 0 1px 4px rgba(15,23,42,0.06)',
        fontFamily: "'IBM Plex Sans', 'Segoe UI', sans-serif",
      }}
    >
      {/* ── Top row: brand + search ───────────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap', marginBottom: 10 }}>
        {/* Brand */}
        <div style={{ flexShrink: 0 }}>
          <p style={{
            fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
            letterSpacing: '0.22em', color: '#0ea5e9', margin: '0 0 2px',
          }}>
            India Industrial Intelligence
          </p>
          <h1 style={{ fontSize: 17, fontWeight: 800, color: '#0f172a', margin: 0, lineHeight: 1.1 }}>
            Industrial GIS Platform
          </h1>
        </div>

        {/* Divider */}
        <div style={{ width: 1, height: 36, background: '#e2e8f0', flexShrink: 0 }} className="hidden-mobile" />

        {/* Search bar */}
        <form
          onSubmit={e => { e.preventDefault(); onSearch(); }}
          style={{ flex: '1 1 300px', display: 'flex', gap: 8, alignItems: 'center' }}
        >
          <div style={{ flex: 1, position: 'relative' }}>
            <span style={{
              position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)',
              fontSize: 14, color: '#94a3b8', pointerEvents: 'none', lineHeight: 1,
            }}>🔍</span>
            <input
              id="site-search-input"
              type="search"
              placeholder="Search by name, state, district, or industry…"
              value={searchDraft}
              onChange={e => onSearchDraftChange(e.target.value)}
              style={{
                width: '100%', boxSizing: 'border-box',
                padding: '8px 12px 8px 36px',
                borderRadius: 10,
                border: '1px solid #e2e8f0',
                background: '#f8fafc',
                fontSize: 13, color: '#0f172a', outline: 'none',
                transition: 'border-color 0.15s, background 0.15s',
                fontFamily: "'IBM Plex Sans', sans-serif",
              }}
              onFocus={e => { e.currentTarget.style.borderColor = '#0ea5e9'; e.currentTarget.style.background = '#fff'; }}
              onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#f8fafc'; }}
            />
            {isLoading && (
              <span style={{
                position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)',
                width: 14, height: 14, borderRadius: '50%',
                border: '2px solid #e2e8f0', borderTopColor: '#0ea5e9',
                animation: 'spin 0.7s linear infinite',
                display: 'inline-block',
              }} />
            )}
          </div>

          <button
            type="submit"
            id="search-submit-btn"
            style={{
              padding: '8px 16px', borderRadius: 10, border: 'none',
              background: '#0f172a', color: '#fff',
              fontSize: 13, fontWeight: 600, cursor: 'pointer',
              flexShrink: 0, transition: 'background 0.15s',
              fontFamily: "'IBM Plex Sans', sans-serif",
            }}
            onMouseEnter={e => (e.currentTarget.style.background = '#1e293b')}
            onMouseLeave={e => (e.currentTarget.style.background = '#0f172a')}
          >
            Search
          </button>

          <button
            type="button"
            id="search-reset-btn"
            onClick={onReset}
            style={{
              padding: '8px 14px', borderRadius: 10,
              border: '1px solid #e2e8f0', background: '#fff',
              color: '#64748b', fontSize: 13, fontWeight: 500,
              cursor: 'pointer', flexShrink: 0, transition: 'all 0.15s',
              fontFamily: "'IBM Plex Sans', sans-serif",
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = '#cbd5e1'; e.currentTarget.style.background = '#f8fafc'; }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#fff'; }}
          >
            Reset
          </button>
        </form>
      </div>

      {/* ── Filter row ────────────────────────────────────────────────────── */}
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span style={{ fontSize: 11, fontWeight: 600, color: '#94a3b8', textTransform: 'uppercase', letterSpacing: '0.07em', flexShrink: 0 }}>
          Filter:
        </span>

        <select
          id="filter-state"
          value={selectedState}
          onChange={e => onStateChange(e.target.value)}
          style={selectStyle}
          onFocus={e => { e.currentTarget.style.borderColor = '#0ea5e9'; e.currentTarget.style.background = '#fff'; }}
          onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#f8fafc'; }}
        >
          <option value="">All States</option>
          {(options?.states ?? []).map(s => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          id="filter-district"
          value={selectedDistrict}
          onChange={e => onDistrictChange(e.target.value)}
          disabled={districts.length === 0}
          style={{ ...selectStyle, opacity: districts.length === 0 ? 0.5 : 1 }}
          onFocus={e => { e.currentTarget.style.borderColor = '#0ea5e9'; e.currentTarget.style.background = '#fff'; }}
          onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#f8fafc'; }}
        >
          <option value="">{selectedState ? 'All Districts' : 'All Districts'}</option>
          {districts.map(d => (
            <option key={d} value={d}>{d}</option>
          ))}
        </select>

        <select
          id="filter-industry-type"
          value={selectedIndustryType}
          onChange={e => onIndustryTypeChange(e.target.value)}
          style={selectStyle}
          onFocus={e => { e.currentTarget.style.borderColor = '#0ea5e9'; e.currentTarget.style.background = '#fff'; }}
          onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#f8fafc'; }}
        >
          <option value="">All Industries</option>
          {(options?.industry_types ?? []).map(t => (
            <option key={t} value={t}>{t.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
          ))}
        </select>

        <select
          id="filter-status"
          value={selectedStatus}
          onChange={e => onStatusChange(e.target.value)}
          style={selectStyle}
          onFocus={e => { e.currentTarget.style.borderColor = '#0ea5e9'; e.currentTarget.style.background = '#fff'; }}
          onBlur={e => { e.currentTarget.style.borderColor = '#e2e8f0'; e.currentTarget.style.background = '#f8fafc'; }}
        >
          <option value="">All Statuses</option>
          {(options?.statuses ?? []).map(s => (
            <option key={s} value={s}>{s.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>
          ))}
        </select>

        {/* Active filter pills */}
        {(selectedState || selectedDistrict || selectedIndustryType || selectedStatus) && (
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginLeft: 4 }}>
            {[
              selectedState && { label: selectedState, clear: () => onStateChange('') },
              selectedDistrict && { label: selectedDistrict, clear: () => onDistrictChange('') },
              selectedIndustryType && { label: selectedIndustryType.replace(/_/g, ' '), clear: () => onIndustryTypeChange('') },
              selectedStatus && { label: selectedStatus.replace(/_/g, ' '), clear: () => onStatusChange('') },
            ].filter(Boolean).map((pill, i) => pill && (
              <span
                key={i}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 4,
                  padding: '3px 8px 3px 10px', borderRadius: 99,
                  background: '#eff6ff', border: '1px solid #bfdbfe',
                  fontSize: 11, fontWeight: 600, color: '#1d4ed8',
                  cursor: 'default',
                }}
              >
                {pill.label}
                <button
                  onClick={pill.clear}
                  style={{
                    width: 14, height: 14, borderRadius: '50%', border: 'none',
                    background: '#bfdbfe', color: '#1d4ed8',
                    fontSize: 10, cursor: 'pointer', padding: 0, lineHeight: 1,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                  }}
                >×</button>
              </span>
            ))}
          </div>
        )}
      </div>
    </header>
  );
}
