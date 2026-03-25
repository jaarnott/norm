export const metadata = { title: 'Privacy Policy – Norm' };

const sectionStyle = { marginBottom: '2rem' };
const h2Style = { fontSize: '1.3rem', fontWeight: 700 as const, color: '#2d2a26', marginBottom: '0.75rem' };
const pStyle = { color: '#555', lineHeight: 1.8, fontSize: '0.95rem', marginBottom: '0.75rem' };

export default function PrivacyPage() {
  return (
    <div style={{ maxWidth: 780, margin: '0 auto', padding: '3rem 2rem 5rem' }}>
      <h1 style={{ fontSize: '2rem', fontWeight: 800, color: '#2d2a26', marginBottom: '0.5rem' }}>Privacy Policy</h1>
      <p style={{ color: '#999', fontSize: '0.85rem', marginBottom: '2.5rem' }}>Last updated: March 25, 2026</p>

      <div style={sectionStyle}>
        <h2 style={h2Style}>1. Introduction</h2>
        <p style={pStyle}>Norm (&quot;we&quot;, &quot;us&quot;, or &quot;our&quot;) operates the bettercallnorm.com website and platform. This Privacy Policy explains how we collect, use, disclose, and safeguard your information when you use our service.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>2. Information We Collect</h2>
        <p style={pStyle}><strong>Account Information:</strong> When you create an account, we collect your name, email address, and organisation details.</p>
        <p style={pStyle}><strong>Usage Data:</strong> We automatically collect information about how you interact with the platform, including pages visited, features used, and timestamps.</p>
        <p style={pStyle}><strong>Third-Party Authentication:</strong> If you sign in via Google or another OAuth provider, we receive your name, email, and profile picture as authorised by you.</p>
        <p style={pStyle}><strong>Business Data:</strong> Data you enter into the platform such as employee rosters, procurement records, and operational information.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>3. How We Use Your Information</h2>
        <p style={pStyle}>We use the information we collect to:</p>
        <ul style={{ ...pStyle, paddingLeft: '1.5rem' }}>
          <li>Provide, maintain, and improve our services</li>
          <li>Process transactions and send related information</li>
          <li>Send administrative notifications such as updates and security alerts</li>
          <li>Respond to your comments, questions, and support requests</li>
          <li>Monitor and analyse usage trends to improve user experience</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>4. Data Sharing &amp; Disclosure</h2>
        <p style={pStyle}>We do not sell your personal information. We may share information with:</p>
        <ul style={{ ...pStyle, paddingLeft: '1.5rem' }}>
          <li><strong>Service providers</strong> who assist in operating our platform (hosting, analytics, email delivery)</li>
          <li><strong>Legal authorities</strong> when required by law or to protect our rights</li>
          <li><strong>Business transfers</strong> in connection with a merger, acquisition, or sale of assets</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>5. Data Security</h2>
        <p style={pStyle}>We implement industry-standard security measures including encryption in transit (TLS) and at rest, access controls, and regular security reviews. However, no method of transmission over the Internet is 100% secure.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>6. Data Retention</h2>
        <p style={pStyle}>We retain your information for as long as your account is active or as needed to provide services. You may request deletion of your account and associated data at any time by contacting us.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>7. Your Rights</h2>
        <p style={pStyle}>Depending on your jurisdiction, you may have the right to access, correct, delete, or export your personal data. To exercise these rights, contact us at privacy@bettercallnorm.com.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>8. Cookies</h2>
        <p style={pStyle}>We use essential cookies to maintain your session and preferences. We do not use third-party advertising cookies.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>9. Changes to This Policy</h2>
        <p style={pStyle}>We may update this Privacy Policy from time to time. We will notify you of any material changes by posting the new policy on this page and updating the &quot;Last updated&quot; date.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>10. Contact Us</h2>
        <p style={pStyle}>If you have questions about this Privacy Policy, please contact us at privacy@bettercallnorm.com.</p>
      </div>
    </div>
  );
}
