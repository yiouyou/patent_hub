// Copyright (c) 2025, sz and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Docx Proofreading", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Docx Proofreading', {
  refresh(frm) {
    frm.add_custom_button(__('→ Claims To Docx'), () => {
      if (frm.doc.claims_to_docx_id) {
        frappe.set_route('Form', 'Claims To Docx', frm.doc.claims_to_docx_id);
      } else {
        frappe.msgprint(__('No associated Claims To Docx found.'));
      }
    });
    frm.add_custom_button(__('+ Upload Final Docx'), () => {
      if (!frm.doc.is_done) {
        frappe.show_alert({ message: '任务未完成，不能下一步。', indicator: 'red' }, 7);
        return;
      }
      frappe.new_doc('Upload Final Docx', {}, (doc) => {
        doc.patent_title = frm.doc.patent_title
        doc.writer_id = frm.doc.writer_id
        doc.patent_id = frm.doc.patent_id
        doc.scene_to_tech_id = frm.doc.scene_to_tech_id
        doc.tech_to_claims_id = frm.doc.tech_to_claims_id
        doc.claims_to_docx_id = frm.doc.claims_to_docx_id
        doc.docx_proofreading_id = frm.doc.docx_proofreading_id
        doc.save();
      });
    });
  }
});
