import { AuditEvent } from '../../models/audit.model';

// IP addresses use the RFC 5737 documentation ranges (TEST-NET-2/TEST-NET-3).
export const MOCK_AUDIT_EVENTS: AuditEvent[] = [
  {
    id: 'aud-001', timestamp: '2026-04-17T12:30:00Z', userId: 'usr-006', userName: 'Frank Wilson',
    action: 'delete', resourceType: 'File', resourceName: 'old-backup.zip',
    details: 'Permanently deleted file from trash', ipAddress: '198.51.100.15', severity: 'warning',
  },
  {
    id: 'aud-002', timestamp: '2026-04-17T11:00:00Z', userId: 'usr-009', userName: 'Irene Garcia',
    action: 'upload', resourceType: 'File', resourceName: 'Blog-Post-Draft.docx',
    details: 'Uploaded new file (2.4 MB)', ipAddress: '198.51.100.12', severity: 'info',
  },
  {
    id: 'aud-003', timestamp: '2026-04-17T10:30:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'upload', resourceType: 'File', resourceName: 'Q4-Report.docx',
    details: 'Uploaded new file (5.1 MB)', ipAddress: '198.51.100.10', severity: 'info',
  },
  {
    id: 'aud-004', timestamp: '2026-04-17T09:15:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'share', resourceType: 'Document', resourceName: 'Budget-2026.xlsx',
    details: 'Shared with bob.martinez@otterworks.io (editor)', ipAddress: '198.51.100.10', severity: 'info',
  },
  {
    id: 'aud-005', timestamp: '2026-04-17T09:00:00Z', userId: 'usr-002', userName: 'Bob Martinez',
    action: 'create', resourceType: 'Folder', resourceName: 'Marketing-Assets',
    details: 'Created new folder', ipAddress: '198.51.100.25', severity: 'info',
  },
  {
    id: 'aud-006', timestamp: '2026-04-17T08:20:00Z', userId: 'usr-005', userName: 'Emily Davis',
    action: 'share', resourceType: 'Document', resourceName: 'Product-Roadmap.docx',
    details: 'Shared with engineering team (viewer)', ipAddress: '198.51.100.5', severity: 'info',
  },
  {
    id: 'aud-007', timestamp: '2026-04-17T08:00:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'login', resourceType: 'System', resourceName: 'Admin Portal',
    details: 'Successful login from known IP', ipAddress: '198.51.100.10', severity: 'info',
  },
  {
    id: 'aud-008', timestamp: '2026-04-16T22:15:00Z', userId: 'usr-004', userName: 'David Kim',
    action: 'suspend', resourceType: 'User', resourceName: 'david.kim@otterworks.io',
    details: 'Account suspended by admin due to policy violation', ipAddress: '198.51.100.15', severity: 'critical',
  },
  {
    id: 'aud-009', timestamp: '2026-04-16T16:45:00Z', userId: 'usr-003', userName: 'Carol Chen',
    action: 'download', resourceType: 'File', resourceName: 'Design-System.fig',
    details: 'Downloaded file (45.2 MB)', ipAddress: '198.51.100.42', severity: 'info',
  },
  {
    id: 'aud-010', timestamp: '2026-04-16T16:00:00Z', userId: 'usr-001', userName: 'Alice Johnson',
    action: 'update', resourceType: 'Document', resourceName: 'Team-Notes.docx',
    details: 'Document edited (version 14)', ipAddress: '198.51.100.10', severity: 'info',
  },
  {
    id: 'aud-011', timestamp: '2026-04-16T14:30:00Z', userId: 'usr-002', userName: 'Bob Martinez',
    action: 'upload', resourceType: 'File', resourceName: 'Campaign-Brief.pdf',
    details: 'Uploaded new file (1.8 MB)', ipAddress: '198.51.100.25', severity: 'info',
  },
  {
    id: 'aud-012', timestamp: '2026-04-16T10:00:00Z', userId: 'usr-006', userName: 'Frank Wilson',
    action: 'login', resourceType: 'System', resourceName: 'Admin Portal',
    details: 'Failed login attempt (wrong password)', ipAddress: '203.0.113.50', severity: 'warning',
  },
  {
    id: 'aud-013', timestamp: '2026-04-15T15:30:00Z', userId: 'usr-008', userName: 'Henry Patel',
    action: 'create', resourceType: 'Document', resourceName: 'API-Docs-v2.md',
    details: 'Created new document', ipAddress: '198.51.100.80', severity: 'info',
  },
  {
    id: 'aud-014', timestamp: '2026-04-15T12:00:00Z', userId: 'usr-006', userName: 'Frank Wilson',
    action: 'update', resourceType: 'System', resourceName: 'Feature Flag: real-time-collab',
    details: 'Feature flag enabled', ipAddress: '198.51.100.15', severity: 'warning',
  },
  {
    id: 'aud-015', timestamp: '2026-04-15T09:00:00Z', userId: 'usr-010', userName: 'James Thompson',
    action: 'login', resourceType: 'System', resourceName: 'Web App',
    details: 'Successful login', ipAddress: '198.51.100.100', severity: 'info',
  },
];
