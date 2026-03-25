export const metadata = { title: 'Terms of Service – Norm' };

const sectionStyle = { marginBottom: '2rem' };
const h2Style = { fontSize: '1.3rem', fontWeight: 700 as const, color: '#2d2a26', marginBottom: '0.75rem' };
const pStyle = { color: '#555', lineHeight: 1.8, fontSize: '0.95rem', marginBottom: '0.75rem' };

export default function TermsPage() {
  return (
    <div style={{ maxWidth: 780, margin: '0 auto', padding: '3rem 2rem 5rem' }}>
      <h1 style={{ fontSize: '2rem', fontWeight: 800, color: '#2d2a26', marginBottom: '0.5rem' }}>Terms of Service</h1>
      <p style={{ color: '#999', fontSize: '0.85rem', marginBottom: '2.5rem' }}>Last updated: March 25, 2026</p>

      <div style={sectionStyle}>
        <h2 style={h2Style}>1. Acceptance of Terms</h2>
        <p style={pStyle}>By accessing or using Norm (&quot;the Service&quot;), operated at bettercallnorm.com, you agree to be bound by these Terms of Service. If you do not agree, do not use the Service.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>2. Description of Service</h2>
        <p style={pStyle}>Norm is an AI-powered operations management platform for hospitality businesses. The Service includes tools for rostering, procurement, HR management, reporting, and AI-assisted task execution.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>3. Accounts</h2>
        <p style={pStyle}>You must provide accurate and complete information when creating an account. You are responsible for maintaining the security of your account credentials and for all activities under your account.</p>
        <p style={pStyle}>Organisation administrators are responsible for managing access and permissions for members of their organisation.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>4. Acceptable Use</h2>
        <p style={pStyle}>You agree not to:</p>
        <ul style={{ ...pStyle, paddingLeft: '1.5rem' }}>
          <li>Use the Service for any unlawful purpose</li>
          <li>Attempt to gain unauthorised access to any part of the Service</li>
          <li>Interfere with or disrupt the integrity or performance of the Service</li>
          <li>Upload malicious code or content</li>
          <li>Resell or redistribute access to the Service without authorisation</li>
        </ul>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>5. Your Data</h2>
        <p style={pStyle}>You retain ownership of all data you submit to the Service. By using the Service, you grant us a limited licence to process your data solely to provide and improve the Service. We handle your data in accordance with our <a href="/privacy" style={{ color: '#a08060' }}>Privacy Policy</a>.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>6. AI-Generated Content</h2>
        <p style={pStyle}>The Service uses artificial intelligence to generate suggestions, analyses, and automated actions. AI-generated outputs are provided as recommendations and should be reviewed before acting on them. We do not guarantee the accuracy of AI-generated content.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>7. Availability &amp; Support</h2>
        <p style={pStyle}>We aim to maintain high availability but do not guarantee uninterrupted access. We may perform maintenance or updates that temporarily affect availability. We will endeavour to provide notice of planned downtime.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>8. Payment &amp; Billing</h2>
        <p style={pStyle}>Paid plans are billed in accordance with the pricing displayed at the time of subscription. We reserve the right to change pricing with 30 days&apos; notice. Refunds are handled on a case-by-case basis.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>9. Limitation of Liability</h2>
        <p style={pStyle}>To the maximum extent permitted by law, Norm and its operators shall not be liable for any indirect, incidental, special, consequential, or punitive damages, or any loss of profits or revenues, whether incurred directly or indirectly.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>10. Termination</h2>
        <p style={pStyle}>We may suspend or terminate your access to the Service at our discretion if you violate these Terms. You may close your account at any time by contacting us. Upon termination, your right to use the Service ceases immediately.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>11. Changes to Terms</h2>
        <p style={pStyle}>We may modify these Terms at any time. Material changes will be communicated via email or an in-app notification. Continued use of the Service after changes constitutes acceptance of the revised Terms.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>12. Governing Law</h2>
        <p style={pStyle}>These Terms are governed by the laws of Australia. Any disputes arising from these Terms shall be resolved in the courts of New South Wales, Australia.</p>
      </div>

      <div style={sectionStyle}>
        <h2 style={h2Style}>13. Contact Us</h2>
        <p style={pStyle}>If you have questions about these Terms, please contact us at support@bettercallnorm.com.</p>
      </div>
    </div>
  );
}
