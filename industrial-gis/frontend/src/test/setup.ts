import '@testing-library/jest-dom/vitest';
import { afterEach, beforeEach } from 'vitest';
import { cleanup } from '@testing-library/react';

afterEach(() => cleanup());

// the first-visit guide would cover every App test
beforeEach(() => { try { localStorage.setItem('thermotrace.guide.seen', '1'); } catch { /* ignore */ } });
