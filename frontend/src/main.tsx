import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import './styles/fonts.css';
import './styles/tokens.css';
import './styles/base.css';
import './styles/background.css';
import './styles/orb.css';
import './styles/run.css';
import './styles/pages.css';
import App from './App';
import { I18nProvider } from './i18n';
import { AuthProvider } from './auth/AuthContext';

document.addEventListener('visibilitychange', () => document.documentElement.classList.toggle('tab-hidden', document.hidden));

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <I18nProvider>
        <AuthProvider>
          <App />
        </AuthProvider>
      </I18nProvider>
    </BrowserRouter>
  </StrictMode>,
);
