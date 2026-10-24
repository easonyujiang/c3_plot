import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { App } from './App';
import { ErrorBoundary } from './ErrorBoundary';
import { queryClient } from './query';
import './style.css';

createRoot(document.getElementById('root')!).render(
  <ErrorBoundary><QueryClientProvider client={queryClient}><App/></QueryClientProvider></ErrorBoundary>,
);
