import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import ConflictQueuePage from "./pages/ConflictQueuePage";
import AuditPage from "./pages/AuditPage";
import MappingsPage from "./pages/MappingsPage";
import TriggerPage from "./pages/TriggerPage";

export default function App() {
  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<Navigate to="/conflicts" replace />} />
        <Route path="/conflicts" element={<ConflictQueuePage />} />
        <Route path="/conflicts/resolved" element={<ConflictQueuePage resolved />} />
        <Route path="/audit" element={<AuditPage />} />
        <Route path="/mappings" element={<MappingsPage />} />
        <Route path="/trigger" element={<TriggerPage />} />
        <Route path="*" element={<Navigate to="/conflicts" replace />} />
      </Routes>
    </AppShell>
  );
}